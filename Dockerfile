# ==========================
# FarajaMH / Mental Health App (Linux)
# Python 3.10 + Gunicorn + gevent (Flask-Sock compatible)
# ==========================
FROM python:3.10-slim

# ---- System deps ----
# build-essential/pkg-config: build wheels (PyAudio, webrtcvad)
# portaudio, asound, sndfile: audio libs (only needed if you keep PyAudio)
# ffmpeg: audio processing
# curl: healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc pkg-config \
    portaudio19-dev libasound2-dev libsndfile1 \
    ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Africa/Nairobi

WORKDIR /app
pip install google-genai

# ---- Requirements ----
COPY requirements.txt /app/requirements.txt

# Normalize CRLF/UTF-16 from Windows if needed
RUN python - <<'PY'
p = "/app/requirements.txt"
raw = open(p, "rb").read()
enc = "utf-8"
if raw[:2] in (b"\xff\xfe", b"\xfe\xff"): enc = "utf-16"
try: s = raw.decode(enc)
except Exception: s = raw.decode("utf-16-le", "ignore")
s = s.replace("\r\n","\n").replace("\r","\n")
open("/app/requirements.utf8.txt","wb").write(s.encode("utf-8"))
PY

# Strip Windows-only pkgs if present (safety net)
RUN awk 'tolower($0) !~ /^(pywin32|pypiwin32|pyreadline3|pipwin|comtypes|pywinpty)([=<> ]|$)/' \
      /app/requirements.utf8.txt > /app/requirements.linux.txt

# Ensure WS deps in case they’re missing; DO NOT add gevent-websocket
RUN printf '%s\n%s\n' \
    "flask-sock==0.7.0" \
    "simple-websocket==1.1.0" \
    >> /app/requirements.linux.txt

# Install Python deps
RUN python -m pip install --upgrade pip && \
    pip install -r /app/requirements.linux.txt && \
    pip install gunicorn gevent

# ---- App code ----
COPY . /app

# ---- Non-root user ----
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# ---- Healthcheck ----
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:5000/ || exit 1

# ---- Run with a Flask-Sock compatible worker ----
# IMPORTANT: use gevent (NOT geventwebsocket) and target your factory.
CMD ["gunicorn", "-k", "gevent", "-w", "1", "-b", "0.0.0.0:5000", "app_pkg:create_app()"]
