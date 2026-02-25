from __future__ import annotations

import json
import os
import re
import wave
import time
import uuid
import shutil
import logging
import tempfile
import subprocess
import threading
import queue
from typing import Optional, Dict

import numpy as np
import webrtcvad
from flask import Blueprint, request, jsonify, current_app as app
from flask_wtf.csrf import CSRFProtect  # noqa: F401

from google import genai
from google.genai import types as genai_types

from .. import csrf  # use app_pkg.csrf

try:
    from simple_websocket.errors import ConnectionClosed
except ImportError:
    ConnectionClosed = None  # type: ignore

stt_bp = Blueprint("stt_bp", __name__)
logger = logging.getLogger(__name__)

# Suppress noisy AFC (automatic function calling) INFO from google-genai when not using tools
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

# ----------- ENV / Defaults -----------

# Google AI (Gemini) API key – required for speech-to-text. Get one at https://aistudio.google.com/apikey
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip()

SAMPLE_RATE             = int(os.getenv("SAMPLE_RATE", "16000"))
FFMPEG_BIN              = os.getenv(
    "FFMPEG_BIN",
    "ffmpeg" if os.name != "nt" else r"C:\ffmpeg\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe",
)
VAD_AGGR                = int(os.getenv("STT_VAD_AGGRESSIVENESS", "3"))
VAD_FRAME_MS            = int(os.getenv("VAD_FRAME_MS", "30"))
VAD_RATIO_MIN           = float(os.getenv("VAD_VOICED_RATIO_MIN", "0.65"))
EMIT_PARTIALS           = (os.getenv("EMIT_PARTIALS", "false").lower() == "true")
PARTIAL_MIN_INTERVAL_MS = int(os.getenv("STT_PARTIAL_MIN_INTERVAL_MS", "600"))
SEGMENT_SILENCE_MS      = int(os.getenv("STT_SEGMENT_SILENCE_MS", "1200"))
MAX_SEGMENT_SEC         = float(os.getenv("STT_MAX_SEGMENT_SEC", "10.0"))
# ~0.002 normalized (reference); 80/32768 ≈ 0.0024
RMS_MIN                 = float(os.getenv("STT_RMS_MIN", "80.0"))

if shutil.which(FFMPEG_BIN) is None:
    alt = shutil.which("ffmpeg")
    if alt:
        FFMPEG_BIN = alt

# ----------- Helpers (copied from working Whisper version) -----------


def _debabble(s: str) -> str:
    if not s:
        return s
    s = re.sub(r"\b(\w{1,3})(?:\s+\1){4,}\b", r"\1 \1", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(\w+)(?:\s+\1){2,}\b", r"\1 \1", s, flags=re.IGNORECASE)
    return s.strip()


def _squash_runs(s: str) -> str:
    if not s:
        return s
    return re.sub(
        r"(\b(?:\w+[\s,;:.!?-]+){1,6})\1{2,}",
        r"\1\1",
        s,
        flags=re.IGNORECASE,
    )


def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = _debabble(_squash_runs(s.replace("\uFFFd", "").strip()))
    bad = ["nigga", "nigger"]
    if any(b in s.lower() for b in bad):
        return ""
    return s


def vad_voiced_ratio(pcm_bytes: bytes, sr: int, frame_ms: int = 30, aggressiveness: int = 3) -> float:
    try:
        vad = webrtcvad.Vad(int(aggressiveness))
        frame_len = int(sr * (frame_ms / 1000.0)) * 2
        if frame_len <= 0 or len(pcm_bytes) < frame_len:
            return 0.0
        voiced, total = 0, 0
        for i in range(0, len(pcm_bytes) - frame_len + 1, frame_len):
            chunk = pcm_bytes[i : i + frame_len]
            total += 1
            if vad.is_speech(chunk, sr):
                voiced += 1
        return voiced / max(total, 1)
    except Exception:
        # fail open: treat as speech
        return 1.0


def rms_level(pcm_bytes: bytes) -> float:
    if not pcm_bytes:
        return 0.0
    arr = np.frombuffer(pcm_bytes, dtype=np.int16)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr.astype(np.float64) ** 2)))


def convert_to_wav_16k(src_path: str) -> str:
    dst_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.wav")
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        src_path,
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "wav",
        dst_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return dst_path


def _bytes_to_temp_wav(pcm_bytes: bytes, sr: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm_bytes)
    return wav_path


def start_ffmpeg_decoder():
    """
    Decode WebM/opus from browser to 16kHz mono s16le (same as early_cancer_diagnosis).
    Let FFmpeg auto-detect input format from pipe.
    """
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )


# ----------- Gemini wrapper -----------


class GeminiTranscriber:
    """Thin wrapper around Gemini 2.5 Flash for audio transcription."""

    _client: Optional[genai.Client] = None
    _lock = threading.Lock()

    @classmethod
    def _get_client(cls) -> genai.Client:
        if cls._client is not None:
            return cls._client
        with cls._lock:
            if cls._client is not None:
                return cls._client
            if not GEMINI_API_KEY:
                raise ValueError(
                    "GEMINI_API_KEY (or GOOGLE_API_KEY) not set. "
                    "Add it to .env for speech-to-text. Get a key at https://aistudio.google.com/apikey"
                )
            try:
                cls._client = genai.Client(api_key=GEMINI_API_KEY)
            except Exception:
                logger.exception("Failed to initialize Gemini client")
                raise
            return cls._client

    @classmethod
    def transcribe_wav(cls, wav_path: str, lang: Optional[str] = None) -> str:
        client = cls._get_client()
        with open(wav_path, "rb") as f:
            audio_bytes = f.read()

        lang = (lang or "bilingual").lower()
        if lang in ("english", "en"):
            prompt_text = "Transcribe the audio in English."
        elif lang in ("swahili", "sw", "kiswahili"):
            prompt_text = "Transcribe the audio in Swahili."
        else:
            prompt_text = "Transcribe the audio. The speaker may use English and/or Swahili."

        # Same Content shape as early_cancer_diagnosis (role=user, parts=[text, audio])
        contents = [
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_text(text=prompt_text),
                    genai_types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                ],
            )
        ]
        config = genai_types.GenerateContentConfig(temperature=0.0)

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=contents,
                config=config,
            )
        except Exception:
            logger.exception("Gemini generate_content failed")
            raise

        text = getattr(response, "text", "") or ""
        return text.strip()


# ----------- WebSocket STT (Whisper pipeline, Gemini engine) -----------


def register_ws_routes(sock):
    @sock.route("/ws/stt")
    def ws_stt(ws):
        client_lang = (request.args.get("lang", "bilingual") or "bilingual").strip().lower()

        BYTES_PER_SAMPLE = 2
        FRAME_BYTES = int(SAMPLE_RATE * (VAD_FRAME_MS / 1000.0)) * BYTES_PER_SAMPLE
        # Reference: minimum segment length 0.35s for finalize (Gemini works better with longer clips)
        MIN_SEGMENT_BYTES = int(SAMPLE_RATE * 0.35) * BYTES_PER_SAMPLE

        def send_json(obj: Dict):
            try:
                import json
                ws.send(json.dumps(obj))
            except Exception as e:
                # Normal client disconnect (e.g. user stopped mic) – don't log as error
                if ConnectionClosed is not None and isinstance(e, ConnectionClosed):
                    logger.debug("WS send_json skipped (client closed): %s", e)
                    return
                logger.exception("WS send_json failed")

        try:
            ff = start_ffmpeg_decoder()
        except Exception as e:
            send_json({"type": "error", "message": str(e)})
            return

        ws_buf = b""
        stop = threading.Event()
        MIN_WEBSOCKET_CHUNK = 512

        # Shared counters for meter (approximate; no lock for simplicity)
        bytes_in_total = [0]  # list so ingest() can mutate
        bytes_pcm_total = [0]  # main loop updates

        # Single growing segment (reference pattern): always accumulate; finalize on silence or max time
        segment = bytearray()
        seg_start_ts = time.time()
        last_voiced_ts = time.time()
        # Normalized RMS threshold for "is_voiced" (reference: rms > 0.002 on float32)
        RMS_NORMALIZED_MIN = 0.002

        # Reference: worker puts results on queue; main loop drains and sends. Worker sets flush_event when done.
        job_q: "queue.Queue[bytes]" = queue.Queue(maxsize=6)
        flush_event = threading.Event()

        def worker():
            engine = GEMINI_MODEL_NAME or "gemini"
            while not stop.is_set():
                try:
                    pcm = job_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                wav_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                        wav_path = tf.name
                    with wave.open(wav_path, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(pcm)

                    text = GeminiTranscriber.transcribe_wav(wav_path, lang=client_lang)
                    text = _clean_text(text or "")
                    if text:
                        logger.debug("STT final sent len=%s", len(text))
                        send_json({"type": "final", "text": text, "engine": engine})
                    else:
                        logger.debug("STT Gemini returned empty text for segment len=%s", len(pcm))
                except Exception as e:
                    logger.exception("ASR worker failed")
                    send_json({"type": "error", "message": f"Transcription failed: {e}"})
                finally:
                    if wav_path:
                        try:
                            os.unlink(wav_path)
                        except Exception:
                            pass
                    job_q.task_done()
                    flush_event.set()

        threading.Thread(target=worker, daemon=True).start()

        # Reference: separate thread reads PCM from FFmpeg into queue (0.1s chunks)
        pcm_q: "queue.Queue[bytes]" = queue.Queue()
        CHUNK_BYTES = int(SAMPLE_RATE * 0.1) * 2

        def read_pcm():
            try:
                while not stop.is_set():
                    data = ff.stdout.read(CHUNK_BYTES) if hasattr(ff.stdout, "read") else b""
                    if not data:
                        break
                    bytes_pcm_total[0] += len(data)
                    pcm_q.put(data)
            except Exception:
                logger.debug("read_pcm ended")
            finally:
                stop.set()

        def write_webm():
            nonlocal ws_buf
            try:
                send_json({"type": "meter", "bytes_in": 0, "bytes_pcm": 0})
                while not stop.is_set():
                    frame = ws.receive()
                    if frame is None:
                        break
                    # Control: client sends {"type": "stop"} as text to request flush before close
                    if isinstance(frame, str):
                        try:
                            j = json.loads(frame)
                            if j.get("type") == "stop":
                                stop.set()
                                break
                        except Exception:
                            pass
                        continue
                    n = len(frame) if isinstance(frame, (bytes, bytearray)) else 0
                    if n:
                        bytes_in_total[0] += n
                    ws_buf += frame
                    if len(ws_buf) >= MIN_WEBSOCKET_CHUNK:
                        try:
                            ff.stdin.write(ws_buf)
                            ff.stdin.flush()
                            ws_buf = b""
                        except Exception:
                            logger.exception("FFmpeg stdin write failed")
                            break
            except Exception:
                logger.debug("WS write_webm ended")
            finally:
                stop.set()
                try:
                    if ws_buf:
                        ff.stdin.write(ws_buf)
                        ff.stdin.flush()
                    ff.stdin.close()
                except Exception:
                    pass

        threading.Thread(target=read_pcm, daemon=True).start()
        threading.Thread(target=write_webm, daemon=True).start()

        last_meter_ts = time.time()

        try:
            while not stop.is_set():
                try:
                    block = pcm_q.get(timeout=0.5)
                except queue.Empty:
                    if (time.time() - last_meter_ts) > 1.0:
                        send_json({
                            "type": "meter",
                            "bytes_in": bytes_in_total[0],
                            "bytes_pcm": bytes_pcm_total[0],
                        })
                        last_meter_ts = time.time()
                    continue

                segment.extend(block)
                seg_bytes = bytes(segment)
                voiced_ratio = vad_voiced_ratio(
                    seg_bytes, SAMPLE_RATE, frame_ms=VAD_FRAME_MS, aggressiveness=VAD_AGGR
                )
                rms_norm = rms_level(seg_bytes) / 32768.0 if seg_bytes else 0.0
                is_voiced = voiced_ratio >= VAD_RATIO_MIN and rms_norm > RMS_NORMALIZED_MIN
                if is_voiced:
                    last_voiced_ts = time.time()

                now = time.time()
                silence_ms = (now - last_voiced_ts) * 1000.0
                seg_ms = (now - seg_start_ts) * 1000.0
                should_finalize = (
                    (silence_ms >= SEGMENT_SILENCE_MS and len(segment) >= MIN_SEGMENT_BYTES)
                    or (seg_ms >= MAX_SEGMENT_SEC * 1000.0)
                )
                if should_finalize:
                    if not job_q.full() and len(seg_bytes) >= MIN_SEGMENT_BYTES:
                        vr = vad_voiced_ratio(seg_bytes, SAMPLE_RATE, VAD_FRAME_MS, VAD_AGGR)
                        if vr >= 0.15:
                            job_q.put(seg_bytes)
                            logger.debug("STT segment submitted len=%s voiced_ratio=%.2f", len(seg_bytes), vr)
                    segment.clear()
                    seg_start_ts = time.time()
                    last_voiced_ts = time.time()

                # Drain worker: we send "final" from worker thread; no out-queue to drain (reference worker has q_out)
                if (time.time() - last_meter_ts) > 1.0:
                    send_json({
                        "type": "meter",
                        "bytes_in": bytes_in_total[0],
                        "bytes_pcm": bytes_pcm_total[0],
                    })
                    last_meter_ts = time.time()
        except Exception:
            logger.exception("/ws/stt loop error")
        finally:
            stop.set()
            try:
                if ff:
                    ff.terminate()
            except Exception:
                pass
            # Flush final segment so user gets transcript when they click Stop (reference has no stop message; we do)
            try:
                seg_bytes = bytes(segment)
                if len(seg_bytes) >= MIN_SEGMENT_BYTES and not job_q.full():
                    if vad_voiced_ratio(seg_bytes, SAMPLE_RATE, VAD_FRAME_MS, VAD_AGGR) >= 0.15:
                        flush_event.clear()
                        job_q.put(seg_bytes)
                        flush_event.wait(timeout=8.0)
            except Exception:
                pass


# ----------- Batch transcribe (real actors mode) -----------


@stt_bp.post("/transcribe_audio")
@csrf.exempt
def transcribe_audio():
    try:
        audio = request.files.get("audio")
        lang = (request.form.get("lang") or "bilingual").strip().lower()
        if not audio:
            return jsonify({"error": "No audio uploaded"}), 400

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as temp_audio:
            audio.save(temp_audio.name)
        wav_path = convert_to_wav_16k(temp_audio.name)

        text = GeminiTranscriber.transcribe_wav(wav_path, lang=lang)
        engine = GEMINI_MODEL_NAME

        text = _clean_text(text or "")
        return jsonify({"text": text, "engine": engine})
    except Exception:
        logger.exception("Error during audio transcription")
        return jsonify({"error": "Audio transcription failed"}), 500
    finally:
        try:
            if "temp_audio" in locals():
                os.unlink(temp_audio.name)
        except Exception:
            pass
        try:
            if "wav_path" in locals():
                os.unlink(wav_path)
        except Exception:
            pass
