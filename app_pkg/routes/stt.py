from __future__ import annotations

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
RMS_MIN                 = float(os.getenv("STT_RMS_MIN", "250.0"))

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
    Use the exact same decoder pipeline as the working Whisper app.
    """
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "matroska,webm",
        "-err_detect",
        "ignore_err",
        "-analyzeduration",
        "0",
        "-probesize",
        "32",
        "-fflags",
        "+genpts+igndts",
        "-re",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=1024,
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
        if lang == "english":
            lang_hint = "The conversation is mainly in English (Kenyan context)."
        elif lang == "swahili":
            lang_hint = "The conversation is mainly in Swahili (Kenyan/Tanzanian context)."
        else:
            lang_hint = (
                "The conversation may mix English and Swahili (Kenyan/Tanzanian code-switching)."
            )

        prompt_text = (
            f"{lang_hint} "
            "Transcribe this clinician–patient mental health screening conversation "
            "verbatim. Preserve code-switching and do NOT summarize. "
            "Return only the transcript text, no speaker labels."
        )

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=[
                    prompt_text,
                    genai_types.Part.from_bytes(
                        data=audio_bytes, mime_type="audio/wav"
                    ),
                ],
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
        RING_SECONDS = 14
        RING_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * RING_SECONDS

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

        ring = bytearray()
        ws_buf = b""
        stop = threading.Event()
        MIN_WEBSOCKET_CHUNK = 512

        # Shared counters for meter (approximate; no lock for simplicity)
        bytes_in_total = [0]  # list so ingest() can mutate
        bytes_pcm_total = [0]  # main loop updates

        in_speech = False
        seg_buf = bytearray()
        seg_start_ts = None
        last_voiced_ts = None
        last_emit_partial_ts = 0.0

        job_q: "queue.Queue[bytes]" = queue.Queue(maxsize=6)

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
                        send_json({"type": "final", "text": text, "engine": engine})
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

        threading.Thread(target=worker, daemon=True).start()

        def ingest():
            nonlocal ws_buf
            try:
                send_json({"type": "meter", "bytes_in": 0, "bytes_pcm": 0})
                while not stop.is_set():
                    frame = ws.receive()
                    if frame is None:
                        break
                    if isinstance(frame, str) or not frame:
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
                logger.debug("WS ingest ended")
            finally:
                stop.set()
                try:
                    if ws_buf:
                        ff.stdin.write(ws_buf)
                        ff.stdin.flush()
                    ff.stdin.close()
                except Exception:
                    pass

        threading.Thread(target=ingest, daemon=True).start()

        last_meter_ts = time.time()

        try:
            while not stop.is_set():
                try:
                    chunk = ff.stdout.read(FRAME_BYTES) if hasattr(ff.stdout, "read") else b""
                except Exception:
                    chunk = b""

                if chunk:
                    bytes_pcm_total[0] += len(chunk)
                    ring.extend(chunk)
                    if len(ring) > RING_BYTES:
                        del ring[: -RING_BYTES]

                    # VAD state machine
                    if not in_speech:
                        tail_len = FRAME_BYTES * max(int(1000 / VAD_FRAME_MS), 1)
                        tail = bytes(ring[-tail_len:]) if len(ring) >= tail_len else bytes(ring)
                        if len(tail) >= FRAME_BYTES:
                            vr = vad_voiced_ratio(
                                tail,
                                SAMPLE_RATE,
                                frame_ms=VAD_FRAME_MS,
                                aggressiveness=VAD_AGGR,
                            )
                            if vr >= VAD_RATIO_MIN and rms_level(tail) >= RMS_MIN:
                                in_speech = True
                                seg_buf.extend(tail)
                                seg_start_ts = time.time()
                                last_voiced_ts = time.time()
                    else:
                        seg_buf.extend(chunk)
                        vr_frame = vad_voiced_ratio(
                            chunk,
                            SAMPLE_RATE,
                            frame_ms=VAD_FRAME_MS,
                            aggressiveness=VAD_AGGR,
                        )
                        if vr_frame >= VAD_RATIO_MIN or rms_level(chunk) >= RMS_MIN:
                            last_voiced_ts = time.time()

                        # Optional partials (be careful: each one calls Gemini)
                        if EMIT_PARTIALS and (time.time() - last_emit_partial_ts) * 1000.0 >= PARTIAL_MIN_INTERVAL_MS:
                            tail_win = int(SAMPLE_RATE * 2.0) * 2
                            tail = bytes(seg_buf[-tail_win:]) if len(seg_buf) > tail_win else bytes(seg_buf)
                            if len(tail) >= 3 * FRAME_BYTES:
                                try:
                                    p_wav = _bytes_to_temp_wav(tail, SAMPLE_RATE)
                                    ptext = GeminiTranscriber.transcribe_wav(p_wav, lang=client_lang)
                                    try:
                                        os.unlink(p_wav)
                                    except Exception:
                                        pass
                                    ptext = _clean_text(ptext or "")
                                    if ptext:
                                        send_json(
                                            {
                                                "type": "partial",
                                                "text": ptext,
                                                "engine": GEMINI_MODEL_NAME,
                                            }
                                        )
                                except Exception:
                                    pass
                                finally:
                                    last_emit_partial_ts = time.time()

                        # Segment timeout
                        if seg_start_ts and (time.time() - seg_start_ts) >= MAX_SEGMENT_SEC:
                            if not job_q.full() and len(seg_buf) > FRAME_BYTES * 5:
                                job_q.put(bytes(seg_buf))
                            seg_buf.clear()
                            in_speech = False
                            seg_start_ts = None
                            last_voiced_ts = None

                        # Silence-based end of segment
                        if last_voiced_ts and (time.time() - last_voiced_ts) * 1000.0 >= SEGMENT_SILENCE_MS:
                            if not job_q.full() and len(seg_buf) > FRAME_BYTES * 5:
                                job_q.put(bytes(seg_buf))
                            seg_buf.clear()
                            in_speech = False
                            seg_start_ts = None
                            last_voiced_ts = None

                if (time.time() - last_meter_ts) > 1.0:
                    send_json({
                        "type": "meter",
                        "bytes_in": bytes_in_total[0],
                        "bytes_pcm": bytes_pcm_total[0],
                    })
                    last_meter_ts = time.time()

                time.sleep(0.005)
        except Exception:
            logger.exception("/ws/stt loop error")
        finally:
            stop.set()
            try:
                if ff:
                    ff.terminate()
            except Exception:
                pass
            try:
                if len(seg_buf) > FRAME_BYTES * 5 and not job_q.full():
                    job_q.put(bytes(seg_buf))
            except Exception:
                pass
            t0 = time.time()
            while not job_q.empty() and (time.time() - t0) < 2.0:
                time.sleep(0.05)


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
