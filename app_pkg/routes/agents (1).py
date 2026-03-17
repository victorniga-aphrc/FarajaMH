from __future__ import annotations
from datetime import datetime
from flask import Blueprint, request, jsonify, session, Response, stream_with_context
from flask_login import current_user, login_required
from .. import csrf
from crew_runner import (
    simulate_agent_chat_stepwise,
    real_actor_chat_stepwise,
    live_transcription_stream,
)

# shared suggest state (if present)
try:
    from crew_runner import _SUGGEST_STATE  # type: ignore
except Exception:
    _SUGGEST_STATE = {}

from models import create_conversation, log_message

agents_bp = Blueprint("agents_bp", __name__)


def _active_conversation_id() -> str:
    cid = session.get("conversation_id") or session.get("id")
    if not cid:
        cid = create_conversation(owner_user_id=current_user.id)
    session["conversation_id"] = cid
    session["id"] = cid
    session.setdefault("conv", [])
    return cid

def _normalize_roles(raw_roles) -> set[str]:
    """
    roles might be:
      - list[str]
      - list[Role] objects with .name
      - None
    normalize -> lowercase set[str]
    """
    out: set[str] = set()
    for r in (raw_roles or []):
        if isinstance(r, str):
            out.add(r.strip().lower())
        else:
            name = getattr(r, "name", None)
            if name:
                out.add(str(name).strip().lower())
    return out

def _is_admin(user_roles: set[str]) -> bool:
    # include whichever admin labels you use in your DB
    return ("admin" in user_roles) or ("superadmin" in user_roles)

@agents_bp.get('/agent_chat_stream')
@login_required
def agent_chat_stream():
    if not current_user.is_authenticated:
        return "Forbidden", 403

    message = request.args.get('message', '').strip()
    language = request.args.get('lang', 'bilingual').strip().lower()
    role = request.args.get('role', 'patient').strip().lower()
    mode = request.args.get('mode', 'real').strip().lower()
    suggest = request.args.get('suggest', 'stream').strip().lower()

    if not message:
        return jsonify({'error': 'No message provided'}), 400

    # ---------------------------------------------------------
    # ✅ ENFORCE MODE/ROLE BY USER TYPE (server-side safety net)
    # ---------------------------------------------------------
    try:
        user_roles = _normalize_roles(getattr(current_user, "roles", []) or [])
    except Exception:
        user_roles = set()

    admin_user = _is_admin(user_roles)

    # sanitize role/mode inputs a bit
    allowed_modes = {"real", "simulated", "live"}
    if mode not in allowed_modes:
        mode = "real"

    # FINALIZE is always allowed (button is shown only to clinician/admin in UI)
    if role not in ("patient", "clinician", "finalize", "live"):
        role = "patient"

    if "patient" in user_roles and not admin_user:
        # Patients should NEVER be able to use Real Actors or pretend clinician
        mode = "simulated"
        role = "patient"
        if suggest not in ("stream", "final"):
            suggest = "stream"

    elif ("clinician" in user_roles) and not admin_user:
        # Clinicians: don't allow impersonating patient in real-actors
        # (If you ever want clinician to be able to switch roles, remove this block.)
        if role not in ("clinician", "finalize", "live"):
            role = "clinician"
        # keep your existing clinician UX: they typically operate in live mic mode
        if mode not in ("live", "simulated", "real"):
            mode = "live"

    else:
        # ✅ Admin (or any non-patient/non-clinician role): allow role switching freely
        # role + mode are respected as sent by the UI
        pass
    # ---------------------------------------------------------

    sid = _active_conversation_id()

    conv = session.get('conv', [])
    conv.append({"role": role, "message": message})
    session['conv'] = conv

    def _log_hook(session_id, role_, message_, timestamp_, type_="message"):
        try:
            log_message(session_id, role_, message_, timestamp_, type_)
        except Exception:
            pass

    # ✅ Finalize is mode-agnostic: always summarize from full conversation history
    if role.strip().lower() == 'finalize' or message.strip() == '[Finalize]':
        generator = real_actor_chat_stepwise(
            message,
            language_mode=language,
            speaker_role='finalize',
            conversation_history=conv,
            log_hook=_log_hook,
            session_id=sid,
        )
        return Response(stream_with_context(generator), mimetype='text/event-stream')

    if mode == "simulated":
        generator = simulate_agent_chat_stepwise(
            message,
            language_mode=language,
            conversation_history=conv,
            log_hook=_log_hook,
            session_id=sid,
        )
    elif mode == "live":
        generator = live_transcription_stream(
            message,
            language_mode=language,
            speaker_role=role,
            suggest_mode=suggest,
            conversation_history=conv,
            log_hook=_log_hook,
            session_id=sid,
        )
    else:
        generator = real_actor_chat_stepwise(
            message,
            language_mode=language,
            speaker_role=role,
            conversation_history=conv,
            log_hook=_log_hook,
            session_id=sid,
        )

    return Response(stream_with_context(generator), mimetype='text/event-stream')


@agents_bp.post("/live/mark_asked")
@csrf.exempt
@login_required
def live_mark_asked():
    data = request.get_json(silent=True) or {}
    _ = (data.get("text") or "").strip()
    sid = session.get("conversation_id") or session.get("id")
    if not sid:
        return jsonify({"ok": False, "reason": "no_session"}), 400
    st = _SUGGEST_STATE.setdefault(sid, {"asked": 0, "buffer": []})
    st["asked"] = int(st.get("asked") or 0) + 1
    return jsonify({"ok": True, "asked": st["asked"]})

@agents_bp.post("/live/reset_plan")
@csrf.exempt
@login_required
def live_reset_plan():
    sid = session.get("conversation_id") or session.get("id")
    if sid:
        _SUGGEST_STATE.pop(sid, None)
    return jsonify({"ok": True})
