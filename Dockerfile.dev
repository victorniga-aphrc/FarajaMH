# ==========================
# FarajaMH / Mental Health App (Linux)
# Python 3.13 + Gunicorn + gevent (Flask-Sock compatible)
# ==========================
FROM python:3.13-slim

# ---- System deps ----
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

# ---- Requirements ----
COPY requirements_python13.txt /app/requirements.txt

# ---- Python tooling hardening (CRITICAL for Python 3.13) ----
RUN python -m pip install --upgrade \
    pip \
    setuptools \
    wheel \
    "packaging<25"

# ---- Install Python deps ----
RUN pip install --no-deps -r /app/requirements.txt || \
    pip install -r /app/requirements.txt

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
CMD ["gunicorn", "-k", "gevent", "-w", "1", "-b", "0.0.0.0:5000", "app_pkg:create_app()"]
