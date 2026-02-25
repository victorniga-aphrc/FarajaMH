# Changelog

All notable changes to the Mental Health Screening App (FarajaMH) are documented here.

---

## [Unreleased] – Phase 1 reference parity

### Security and data access

- **Conversation ownership**
  - Added `get_conversation_if_owned_by(conversation_id, user_id)` in `models.py` to fetch a conversation only when the user owns it.
  - Added `list_conversations_for_user(user_id)` to return conversations for a user with message count and preview.
  - Added `delete_conversation_if_owned_by(conversation_id, user_id)` and `delete_conversation_by_id(conversation_id)` for safe deletion.
- **Admin and clinician scope**
  - `/admin/api/conversation/<cid>` and `/admin/api/conversation/<cid>/disease_likelihoods` now enforce access: clinicians see only their own conversations; admins see all.
  - Admin conversation list already filtered by role; detail and disease-likelihood endpoints aligned with the same rules.

### History and persistence

- **Session key normalization**
  - Standardized on both `session['conversation_id']` and `session['id']` so agents, FAISS, and misc routes use the same active conversation.
  - `app_pkg/__init__.py` `ensure_conversation()` keeps both keys in sync and creates a conversation when missing.
- **History APIs**
  - `GET /api/my-conversations` – list current user’s conversations (id, created_at, message_count, preview).
  - `GET /api/conversations/<id>/messages` – messages for a conversation (ownership enforced; admins can access any).
  - `DELETE /api/conversations/<id>` – delete a conversation if the current user owns it (CSRF-exempt for fetch).
  - `GET /new_conversation` – create a new conversation, set session, redirect to index.
- **History page**
  - New route `GET /history` and template `templates/history.html` with a sidebar list and detail pane.
  - New `static/js/history.js`: load list, select conversation, view messages, delete owned conversation, “New” link to `/new_conversation`.

### UI and navigation

- **Navigation (base.html)**
  - Removed duplicate Dashboard entries: single “Admin Dashboard” for admin, single “Clinician Dashboard” for clinician (no double icons).
  - Distinct icons: Admin = `fa-gauge-high`, Clinician = `fa-user-doctor`, Clinicians = `fa-user-md`.
  - Added “History” link for authenticated users (`/history`).
  - Navbar shows user display name (or email) next to Log out.
- **Admin dashboard**
  - Header actions: “History” and “Back to App”.
  - Conversations table: new “Actions” column with View and Delete per row.
  - Owner column shows display name and email where available.
- **Admin API**
  - `DELETE /admin/api/conversation/<cid>` – admins can delete any conversation; clinicians only their own.
  - Conversation list response includes `owner_display_name`; top clinicians summary includes `display_name`.

### Username and display name

- **Database**
  - Added nullable, unique `username` column on `users` (Alembic migration `9d7a6b3f41c2_add_username_to_users`).
- **Model**
  - `User.display_name` property: `username` → `name` → email local part → email.
- **Auth**
  - Login and `/auth/me` responses include `username` and `display_name`.
  - Signup accepts optional `username`; uniqueness checked.
- **Admin**
  - New clinicians get an auto-generated unique `username` (from name or email).
  - Admin conversation and top-clinician payloads use display name for UI.

### Live transcription (STT)

- **Server (app_pkg/routes/stt.py)**
  - Replaced ring-buffer + “in_speech” gate with a single growing segment (reference-style): all PCM is accumulated and finalized on 1.2 s silence (min 0.35 s audio) or 10 s max segment.
  - Separate `read_pcm` thread fills a queue; main loop consumes from queue with timeout so it can exit when stop is set.
  - Client can send JSON `{"type": "stop"}` to request flush; server flushes final segment to the worker, waits for transcription, then handler returns so the client receives the “final” message before the connection closes.
  - Worker sets a `flush_event` after each job so the main thread can wait for the last segment to be sent before closing.
- **Client (static/js/app.js)**
  - On Stop: send `{"type": "stop"}` instead of closing the WebSocket immediately; show “Stopping…” and wait up to 6 s for “final” messages, then close or timeout.
  - Ensures the last spoken segment is transcribed and displayed before the connection ends.

### Files touched

- **Backend:** `models.py`, `admin.py`, `auth.py`, `app_pkg/__init__.py`, `app_pkg/routes/agents.py`, `app_pkg/routes/misc.py`, `app_pkg/routes/faiss_routes.py`, `app_pkg/routes/stt.py`
- **Templates:** `templates/base.html`, `templates/admin.html`, `templates/history.html` (new)
- **Frontend:** `static/js/app.js`, `static/js/admin.js`, `static/js/history.js` (new)
- **Migrations:** `alembic/versions/9d7a6b3f41c2_add_username_to_users.py` (new)

### Reset, login and mode change (conversation)

- **Reset button**
  - `POST /reset_conv` is now CSRF-exempt so the Reset button works reliably.
  - Reset logic moved into `window.resetConversationAndUI()`; server creates a new conversation and clears session `conv`; client clears transcript and suggested-question UI.
- **New user login**
  - After successful login, session keys `conversation_id`, `id`, and `conv` are cleared so the new user gets a fresh conversation on the next request (no cross-user conversation mix-up).
- **Mode change**
  - Changing chat mode (Real Actors ↔ Simulated ↔ Live) calls `resetConversationAndUI()` so each mode uses a new conversation and the UI is cleared (no mixing of modes in one thread).

### Home page (no login flash)

- **Index for authenticated users**
  - Index template sets initial visibility from `current_user.is_authenticated`: auth-gate is hidden and app-wrapper shown when logged in, so navigating to Home no longer shows the login form briefly before the app.
  - App init JS no longer hides both gate and app on load; it leaves server-rendered visibility until after `/auth/me` and then applies `showApp` / `showAuth` as before.

### Post-upgrade steps

- Run `alembic stamp 87ddf6cf4ec2` if the DB already had the initial schema but no Alembic version.
- Run `alembic upgrade head` to add the `username` column.
- Restart the app so new routes and session behavior are active.

---

*Format inspired by [Keep a Changelog](https://keepachangelog.com/).*
