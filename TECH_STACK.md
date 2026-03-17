## Tech stack – FarajaMH (Mental Health Screening App)

### Backend

- **Language**: Python 3.12 / 3.13 (Docker images use 3.13, local dev 3.12).
- **Web framework**: Flask
  - Blueprint-based architecture: `auth`, `admin`, `misc_bp`, `faiss_bp`, `stt_bp`, optional `agents_bp`.
  - CSRF protection via `Flask-WTF` (`CSRFProtect`).
  - Session + auth via `Flask-Login`.
- **Application factory**: `app_pkg.create_app()` in `app_pkg/__init__.py`
  - Centralizes config loading (.env + `config.Config`).
  - Registers blueprints, CSRF, mail, WebSocket routes, FAISS, and DB init.
- **Server runtime**
  - **Gunicorn** with **gevent** worker (`-k gevent`) for WebSocket-compatible concurrency.
  - Entry points:
    - Local: `python app.py` (uses embedded Gunicorn+gevent when available; falls back to Flask dev server).
    - Docker: `CMD ["gunicorn", "-k", "gevent", "-w", "1", "-b", "0.0.0.0:5000", "app_pkg:create_app()"]`.

### Data & persistence

- **ORM / DB layer**: SQLAlchemy
  - Models in `models.py`: `User`, `Role`, `Conversation`, `Message`, `ScreeningEvent`, `Institution`, `Patient`, etc.
  - Many-to-many roles via `user_roles` association table.
  - Ownership fields (e.g. `Conversation.owner_user_id`) used to scope clinician/admin data.
- **Migrations**: Alembic
  - Schema managed via versioned migrations in `alembic/versions/`.
  - Recent migrations include:
    - `87ddf6cf4ec2_initial_schema.py`
    - `9d7a6b3f41c2_add_username_to_users.py`
    - `2f8c1a9d4b10_add_patients_and_conversation_patient.py` (SQLite-safe).
- **Database engines**
  - **PostgreSQL** (recommended; used in Docker dev setup).
  - SQLite supported for local/dev via Alembic’s SQLite-friendly migrations.
- **Caching / files**
  - FAISS index and metadata files under `data/faiss/` (configurable via env).

### AI, agents & retrieval

- **CrewAI** (`crewai`) for orchestrating AI agents and tasks
  - Orchestration code in `crew_runner.py`.
  - Config-driven agents and tasks loaded from:
    - `config/agents.yaml`
    - `config/tasks.yaml`
  - `simulate_agent_chat_stepwise()` and `real_actor_chat_stepwise()` implement Simulated and Real-Actors flows.
- **FAISS-based retrieval**
  - Custom wrapper (`mental_health_faiss`) loaded in `crew_runner.py`.
  - Two possible index types:
    - Cases index (`search_similar_cases`).
    - Questions index (bilingual question bank).
  - Used for:
    - Question recommendation (`/faiss/suggest_question`, `/faiss/search`, `/questions/search`).
    - Admin FAISS summaries.
- **LLM provider**
  - LLM loading abstracted via `agent_loader.load_llm()` (configurable).
  - Agents (e.g. clinician, listener, recommender) defined in YAML and run via CrewAI.

### Speech-to-text & audio

- **Live transcription**
  - WebSocket endpoint: `/ws/stt` (in `app_pkg/routes/stt.py`).
  - Audio pipeline:
    - Browser sends `audio/webm;codecs=opus` via `MediaRecorder`.
    - Server uses `ffmpeg` to decode to 16 kHz mono PCM (`s16le`).
    - Voice activity detection via `webrtcvad` + RMS gating.
    - Segments pushed through **Google Gemini** (via `google-generativeai`) for final transcripts.
  - STT engine wrapper: `GeminiTranscriber` in `stt.py`.
- **Batch transcription (voice note)**
  - Endpoint: `/transcribe_audio`.
  - Accepts uploaded WebM audio, converts to WAV, calls Gemini STT, and injects resulting text into the turn-based chat.
- **Audio tooling**
  - `ffmpeg` for decoding/conversion (installed in Docker).
  - `webrtcvad`, `numpy`, `wave` for segmentation/analysis.

### Frontend

- **HTML templating**: Jinja2 (Flask templates)
  - Core pages:
    - `index.html` – auth gate, Question Bank, agents conversation UI, patient selector, mode/role controls.
    - `admin.html` / `clinician_dashboard.html` – dashboards, KPIs, conversations, symptom word cloud.
    - `history.html` – per-user conversation history.
    - `clinicians.html` – User Management (admins & clinicians).
    - `profile.html` – clinician/admin profile page.
- **Styling & layout**
  - **Bootstrap 5.3** (via CDN).
  - **Font Awesome 6** for icons.
  - Custom CSS in `static/css/styles.css` (cards, KPIs, dark/light theme, transcript styling, etc.).
- **JavaScript**
  - Vanilla JS (no heavy front-end framework).
  - Main entry: `static/js/app.js`
    - Auth gate handling (`/auth/login`, `/auth/me`, `/auth/logout`).
    - Agent conversation UI:
      - Modes: **Real Actors**, **Simulated**, **Live (Mic)**.
      - Role selector (Clinician/Patient).
      - Pause/Resume and Reset controls.
      - SSE client for `/agent_chat_stream` (turn-based + finalize).
      - Live Mic WebSocket client for `/ws/stt`.
    - Screening integration (`/mh/screen`) with rolling PHQ/GAD/psychosis summary chip section.
    - Patient context:
      - Patient selector and creation (`/api/patients`, `/api/select-patient`).
      - Active patient badge and enforcement of “select patient first” for clinicians/admins.
  - Admin JS: `static/js/admin.js`
    - Dashboards: KPIs, charts (Chart.js), symptom word cloud (WordCloud2).
    - Conversation browser (View/Delete, disease likelihoods).
    - Top clinicians table.
    - Clinician add/edit flows (user management, institution linkage).
  - History JS: `static/js/history.js`
    - Uses `/api/my-conversations` + `/api/conversations/<id>/messages`.
    - Shows conversation metadata (including patient identifier) and transcript.
  - Charts & visualizations:
    - **Chart.js** for basic time series (conversations per day) and other graphs.
    - **wordcloud2.js** for symptom word cloud.

### Authentication & security

- **Auth & roles**
  - Email/password-based login via `/auth/login`.
  - Roles: `admin`, `clinician`, `patient`.
  - Role-based behavior:
    - Admin: global dashboards, user management, and patient lists.
    - Clinician: sees only own conversations/patients, has Live Mic and Simulated modes.
    - Patient: limited access (simulated mode only).
- **Password handling**
  - Hashing via Argon2 (`argon2-cffi`).
  - Password reset flow via signed tokens and email links.
  - OTP verification for signup flows (patients).
- **CSRF & cookies**
  - CSRF protected endpoints via `Flask-WTF` (JSON auth APIs exempted where appropriate).
  - Secure cookie settings depending on `DEBUG` (HTTPOnly, SameSite, Secure).
- **Email**
  - Transactional emails (OTP, reset links, onboarding) via Mailjet (`mailjet-rest`) and Flask-Mail.

### Deployment & ops

- **Docker**
  - Base image: `python:3.13-slim`.
  - `Dockerfile` and `Dockerfile.dev`:
    - Install system dependencies (`ffmpeg`, audio libs, build tools).
    - Install app requirements from `requirements_python13.txt`.
    - Run as non-root `appuser`.
  - `docker-compose.dev.yml` / `docker-compose.yml`:
    - Web service (`farajamh-web`) exposing port `5000` inside container (mapped to host).
    - Env-configured `DATABASE_URL/URI`, FAISS paths, Mailjet, etc.
- **Reverse proxy / TLS (optional)**
  - `deploy/nginx/` contains sample Nginx config + Certbot README for HTTPS termination.

### Configuration & environment

- **Config class**: `config.Config`
  - Reads from environment variables and `.env` file (via `python-dotenv`).
  - Keys include DB connection, FAISS paths, mail settings, and Gemini API keys.
- **Environment variables (key examples)**
  - `DATABASE_URL` / `DATABASE_URI` – SQLAlchemy DB URL.
  - `FAISS_INDEX_PATH`, `FAISS_METADATA_PATH`, `QUESTIONS_JSON_PATH` – FAISS and question-bank files.
  - `GEMINI_API_KEY` / `GOOGLE_API_KEY` – for Google Gemini STT.
  - `SECRET_KEY` – Flask secret key.
  - Mail-related: `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, Mailjet API keys, etc.

### Testing & CI

- **Unit / integration tests**
  - The project uses `pytest` (wired in CI workflow).
- **Linting**
  - `flake8` configured via GitHub Actions workflow to catch syntax and style issues.
- **GitHub Actions**
  - Workflow: `.github/workflows/python-package.yml`
    - Python 3.12 matrix (single-version currently).
    - Installs dependencies, runs flake8 and pytest on push/PR.

