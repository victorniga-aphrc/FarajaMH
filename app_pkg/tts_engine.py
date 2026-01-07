# app_pkg/tts_engine.py
from __future__ import annotations

import os
import re
import wave
import tempfile
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional

from google.cloud import texttospeech

logger = logging.getLogger(__name__)

# Target locales
EN_LOCALE = os.getenv("TTS_EN_LOCALE", "en-KE").strip()  # English (Kenya)
SW_LOCALE = os.getenv("TTS_SW_LOCALE", "sw-KE").strip()  # Swahili (Kenya)

# Optional: force specific voice names (overrides auto-selection)
# If unset, we will auto-pick best female voice (prefers Chirp HD when present).
ENV_VOICE_EN = (os.getenv("TTS_VOICE_EN") or "").strip()
ENV_VOICE_SW = (os.getenv("TTS_VOICE_SW") or "").strip()

# Output WAV settings
OUT_SR = int(os.getenv("TTS_SAMPLE_RATE_HZ", "24000"))  # 24k is a good sweet spot
SPEAKING_RATE_CLINICIAN = float(os.getenv("TTS_RATE_CLINICIAN", "0.95"))
SPEAKING_RATE_DEFAULT = float(os.getenv("TTS_RATE_DEFAULT", "1.0"))

# Simple bilingual heuristics (keep lightweight; your STT is Gemini already)
_SWA_MARKERS = [
    "habari", "hujambo", "karibu", "asante", "pole", "sana",
    "mimi", "wewe", "yangu", "yako", "ninahisi", "nahisi",
    "kwanza", "baada", "kwa", "wa", "ya", "na", "lakini",
    "shida", "tafadhali", "samahani"
]

@dataclass
class _VoicePick:
    language_code: str
    name: Optional[str] = None
    ssml_gender: texttospeech.SsmlVoiceGender = texttospeech.SsmlVoiceGender.FEMALE


_tts_client: Optional[texttospeech.TextToSpeechClient] = None
_voice_cache: dict = {}  # key: locale -> picked voice name (or None)


def _client() -> texttospeech.TextToSpeechClient:
    global _tts_client
    if _tts_client is None:
        _tts_client = texttospeech.TextToSpeechClient()
    return _tts_client


def _list_voice_names(locale: str) -> List[str]:
    try:
        resp = _client().list_voices(language_code=locale)
        return [v.name for v in resp.voices]
    except Exception:
        logger.exception("Failed list_voices for %s", locale)
        return []


def _pick_best_female_voice(locale: str) -> Optional[str]:
    """
    Prefer Chirp 3 HD female if present, otherwise any female voice in locale.
    We do not hardcode names because availability changes.
    """
    if locale in _voice_cache:
        return _voice_cache[locale]

    names = _list_voice_names(locale)
    if not names:
        _voice_cache[locale] = None
        return None

    # Prefer Chirp HD female
    chirp_f = [n for n in names if "Chirp" in n and (n.endswith("-F") or n.endswith("_F") or "-F-" in n)]
    if chirp_f:
        _voice_cache[locale] = chirp_f[0]
        return chirp_f[0]

    # Next best: any voice that looks female-ish by naming
    # (If not, we’ll just let the API choose by locale+FEMALE)
    _voice_cache[locale] = None
    return None


def _is_likely_swahili(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in _SWA_MARKERS)


def _split_bilingual(text: str) -> List[Tuple[str, str]]:
    """
    Split into [("en"|"sw", segment_text), ...].
    Very conservative: if we cannot confidently split, we return one segment.
    """
    text = (text or "").strip()
    if not text:
        return []

    # If the whole thing looks like one language, keep it single.
    if _is_likely_swahili(text):
        return [("sw", text)]

    # Token-level split is messy; do sentence split and label each sentence.
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) <= 1:
        return [("en", text)]

    out: List[Tuple[str, str]] = []
    for s in parts:
        s = s.strip()
        if not s:
            continue
        out.append(("sw" if _is_likely_swahili(s) else "en", s))

    # Merge adjacent same-language segments
    merged: List[Tuple[str, str]] = []
    for lang, seg in out:
        if merged and merged[-1][0] == lang:
            merged[-1] = (lang, merged[-1][1] + " " + seg)
        else:
            merged.append((lang, seg))

    return merged


def _synthesize_pcm(text: str, pick: _VoicePick, speaking_rate: float) -> bytes:
    """
    Returns LINEAR16 PCM bytes at OUT_SR.
    """
    text = (text or "").strip()
    if not text:
        return b""

    # If we have an explicit voice name, use it; else choose best if possible.
    voice_name = pick.name
    if not voice_name:
        voice_name = _pick_best_female_voice(pick.language_code)

    if voice_name:
        voice = texttospeech.VoiceSelectionParams(
            language_code=pick.language_code,
            name=voice_name,
        )
    else:
        voice = texttospeech.VoiceSelectionParams(
            language_code=pick.language_code,
            ssml_gender=pick.ssml_gender,
        )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=OUT_SR,
        speaking_rate=speaking_rate,
    )

    resp = _client().synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=voice,
        audio_config=audio_config,
    )
    return resp.audio_content or b""


def _write_wav(pcm: bytes, sample_rate: int = OUT_SR) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(tmp_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return tmp_path


def synthesize_speech_open(text: str, out_format: str = "wav", *, lang: str = "auto", role: str = "clinician") -> str:
    """
    Drop-in replacement for your previous local VITS engine.
    Returns a temp WAV path.

    lang: "en" | "sw" | "auto"
    role: affects speaking rate a bit (clinician slightly slower)
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty text cannot be synthesized")

    ext = (out_format or "wav").lower()
    if ext != "wav":
        ext = "wav"  # enforce wav (your frontend already handles audio/wav)

    speaking_rate = SPEAKING_RATE_CLINICIAN if (role or "").lower() == "clinician" else SPEAKING_RATE_DEFAULT

    # Voice config (allow env overrides)
    en_pick = _VoicePick(language_code=EN_LOCALE, name=ENV_VOICE_EN or None)
    sw_pick = _VoicePick(language_code=SW_LOCALE, name=ENV_VOICE_SW or None)

    # Decide synthesis strategy
    lang = (lang or "auto").strip().lower()
    if lang in ("sw", "swahili"):
        pcm = _synthesize_pcm(text, sw_pick, speaking_rate)
        return _write_wav(pcm, OUT_SR)

    if lang in ("en", "english"):
        pcm = _synthesize_pcm(text, en_pick, speaking_rate)
        return _write_wav(pcm, OUT_SR)

    # auto / bilingual: split by sentence-level LID and stitch PCM
    segments = _split_bilingual(text)
    if not segments:
        raise ValueError("No text segments to synthesize")

    pcm_all = bytearray()
    for seg_lang, seg_text in segments:
        pick = sw_pick if seg_lang == "sw" else en_pick
        pcm_all.extend(_synthesize_pcm(seg_text, pick, speaking_rate))

        # tiny pause between stitched segments (100ms of silence)
        # 16-bit mono => 2 bytes/sample
        pause_samples = int(0.10 * OUT_SR)
        pcm_all.extend(b"\x00\x00" * pause_samples)

    return _write_wav(bytes(pcm_all), OUT_SR)
