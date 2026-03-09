from __future__ import annotations
from flask import Blueprint, jsonify, render_template, request, session, redirect, url_for
from flask_login import login_required, current_user
from .. import csrf
from screening import run_screening, screening_to_dict
from models import (
    create_conversation,
    create_patient,
    get_patient_for_user,
    latest_conversation_id_for_owner_patient,
    list_all_patients,
    list_patients_for_owner,
    SessionLocal,
    User,
    Role,
    Institution,
    user_roles,
    ScreeningEvent,
    log_message,
    Conversation,
    list_conversations_for_user,
    get_conversation_messages,
    get_conversation_if_owned_by,
    delete_conversation_if_owned_by,
)

from sqlalchemy import desc
from security import hash_password, verify_password
misc_bp = Blueprint("misc_bp", __name__)

import uuid
import os
import base64
import logging
from datetime import datetime

from .. import csrf
from ..core.faiss_core import faiss_system

logger = logging.getLogger(__name__)


from ..tts_engine import synthesize_speech_open

misc_bp = Blueprint("misc_bp", __name__)


def _is_admin(user) -> bool:
    return any(r.name == "admin" for r in getattr(user, "roles", []))


def _is_clinician(user) -> bool:
    return any(r.name == "clinician" for r in getattr(user, "roles", []))


def _requires_patient_context(user) -> bool:
    return _is_admin(user) or _is_clinician(user)


def _active_patient_id() -> int | None:
    try:
        return int(session.get("active_patient_id")) if session.get("active_patient_id") else None
    except Exception:
        return None


def _get_or_create_conversation_id() -> str:
    cid = session.get("conversation_id") or session.get("id")
    if not cid:
        patient_id = _active_patient_id()
        if _requires_patient_context(current_user) and not patient_id:
            raise ValueError("Select a patient before starting a conversation")
        cid = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
    session["conversation_id"] = cid
    session["id"] = cid
    session.setdefault("conv", [])
    return cid

@misc_bp.get('/csrf-token')
def get_csrf_token():
    from flask_wtf.csrf import generate_csrf
    return jsonify({'csrfToken': generate_csrf()})

@misc_bp.get('/health')
def health_check():
    from ..core.faiss_core import faiss_system
    return jsonify({'status': 'healthy', 'faiss_loaded': faiss_system is not None})

@misc_bp.route('/', endpoint='index')
def index():
    if current_user.is_authenticated:
        roles = [role.name for role in getattr(current_user, 'roles', [])]
        needs_reset = getattr(current_user, 'reset_password', False)
        if 'clinician' in roles and needs_reset:
            return redirect(url_for('new-password'))

    return render_template('index.html')


@misc_bp.get('/admin', endpoint='admin')
@login_required
def admin_page():
    if not any(r.name == "admin" for r in current_user.roles):
        return "Forbidden", 403
    return render_template('admin.html')


@misc_bp.post('/reset_conv')
@csrf.exempt
@login_required
def reset_conv():
    session['conv'] = []
    patient_id = _active_patient_id()
    if _requires_patient_context(current_user) and not patient_id:
        return jsonify({"ok": False, "error": "Select a patient before starting a conversation"}), 400
    cid = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
    session['conversation_id'] = cid
    session['id'] = cid
    return jsonify({'ok': True, 'conversation_id': cid})


@misc_bp.post('/conv/log')
@csrf.exempt
@login_required
def conv_log():
    data = (request.get_json(silent=True) or {})
    role = (data.get("role") or "patient").strip()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    if len(text) > 8000:
        text = text[:8000] + " …[truncated]"

    try:
        cid = _get_or_create_conversation_id()
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    log_message(
        cid,
        role=role,
        message=text,
        timestamp=datetime.utcnow().isoformat(timespec="seconds"),
        type_="message",
    )
    return jsonify({"ok": True, "conversation_id": cid})


@misc_bp.post("/mh/screen")
@csrf.exempt
@login_required
def mh_screen():
    """
    IMPORTANT: no transcript persistence here.
    We only compute & store a ScreeningEvent so we don't duplicate messages.
    """
    data = (request.get_json(silent=True) or {})
    transcript = (data.get("transcript") or "").strip()  # used for screening only
    responses = data.get("responses") or {}
    safety = bool(data.get("safety_concerns", False))

    # Run screening using existing API (transcript, responses, safety_concerns)
    out = run_screening(transcript, responses, safety_concerns=safety)
    result = screening_to_dict(out)

    # Persist one ScreeningEvent row for this conversation (no user_id here)
    try:
        cid = _get_or_create_conversation_id()
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    db = SessionLocal()
    try:
        ev = ScreeningEvent(
            id=str(uuid.uuid4()),
            conversation_id=cid,
            overall_flag=result.get("overall_flag"),
            results_json=result,
        )
        db.add(ev)
        db.commit()
    finally:
        db.close()

    return jsonify(result)


@misc_bp.get("/history")
@login_required
def history_page():
    return render_template("history.html")

@misc_bp.get("/profile")
@login_required
def profile_page():
    if not any(r.name in ("admin", "clinician") for r in current_user.roles):
        return "Forbidden", 403
    return render_template("profile.html")


@misc_bp.get("/api/my-conversations")
@login_required
def api_my_conversations():
    return jsonify({"ok": True, "conversations": list_conversations_for_user(current_user.id)})


@misc_bp.get("/api/patients")
@login_required
def api_list_patients():
    if not _requires_patient_context(current_user):
        return jsonify({"ok": False, "error": "Admin or clinician only"}), 403
    patients = list_all_patients() if _is_admin(current_user) else list_patients_for_owner(current_user.id)
    return jsonify({"ok": True, "patients": patients})


@misc_bp.post("/api/patients")
@login_required
def api_create_patient():
    if not _requires_patient_context(current_user):
        return jsonify({"ok": False, "error": "Admin or clinician only"}), 403
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip().upper()
    if not identifier:
        return jsonify({"ok": False, "error": "Patient identifier is required"}), 400
    patient = create_patient(current_user.id, identifier)
    if not patient:
        return jsonify({"ok": False, "error": "Patient identifier already exists"}), 409
    return jsonify({"ok": True, "patient": patient})


@misc_bp.get("/api/current-patient")
@login_required
def api_current_patient():
    pid = _active_patient_id()
    if not pid:
        return jsonify({"ok": True, "patient": None})
    p = get_patient_for_user(pid, current_user.id, is_admin=_is_admin(current_user))
    if not p:
        session.pop("active_patient_id", None)
        return jsonify({"ok": True, "patient": None})
    return jsonify({"ok": True, "patient": {"id": p.id, "identifier": p.identifier, "owner_user_id": p.owner_user_id}})


@misc_bp.post("/api/select-patient")
@login_required
def api_select_patient():
    if not _requires_patient_context(current_user):
        return jsonify({"ok": False, "error": "Admin or clinician only"}), 403
    data = request.get_json(silent=True) or {}
    patient_id = data.get("patient_id")
    continue_latest = bool(data.get("continue_latest", True))
    if not patient_id:
        return jsonify({"ok": False, "error": "patient_id is required"}), 400
    p = get_patient_for_user(int(patient_id), current_user.id, is_admin=_is_admin(current_user))
    if not p:
        return jsonify({"ok": False, "error": "Patient not found"}), 404

    session["active_patient_id"] = p.id
    session["conv"] = []
    if continue_latest:
        cid = latest_conversation_id_for_owner_patient(current_user.id, p.id)
        if not cid:
            cid = create_conversation(owner_user_id=current_user.id, patient_id=p.id)
    else:
        cid = create_conversation(owner_user_id=current_user.id, patient_id=p.id)
    session["conversation_id"] = cid
    session["id"] = cid
    return jsonify({"ok": True, "patient": {"id": p.id, "identifier": p.identifier}, "conversation_id": cid})


@misc_bp.get("/api/conversations/<cid>/messages")
@login_required
def api_conversation_messages(cid):
    is_admin = any(r.name == "admin" for r in current_user.roles)
    convo = get_conversation_if_owned_by(cid, current_user.id)
    if convo is None and not is_admin:
        return jsonify({"ok": False, "error": "Conversation not found"}), 404
    if convo is None and is_admin:
        db = SessionLocal()
        try:
            exists = db.query(Conversation.id).filter(Conversation.id == cid).first()
        finally:
            db.close()
        if not exists:
            return jsonify({"ok": False, "error": "Conversation not found"}), 404

    msgs = get_conversation_messages(cid)
    messages = [
        {
            "id": m.id,
            "role": m.role,
            "type": m.type,
            "message": m.message,
            "timestamp": m.timestamp,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in msgs
    ]
    return jsonify({"ok": True, "conversation_id": cid, "messages": messages})


@misc_bp.delete("/api/conversations/<cid>")
@csrf.exempt
@login_required
def api_delete_conversation(cid):
    ok = delete_conversation_if_owned_by(cid, current_user.id)
    if not ok:
        return jsonify({"ok": False, "error": "Conversation not found"}), 404
    if session.get("conversation_id") == cid or session.get("id") == cid:
        patient_id = _active_patient_id()
        if _requires_patient_context(current_user) and not patient_id:
            session.pop("conversation_id", None)
            session.pop("id", None)
            session["conv"] = []
            return jsonify({"ok": True, "conversation_id": cid})
        session["conversation_id"] = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
        session["id"] = session["conversation_id"]
        session["conv"] = []
    return jsonify({"ok": True, "conversation_id": cid})


@misc_bp.get("/new_conversation")
@login_required
def new_conversation():
    patient_id = _active_patient_id()
    if _requires_patient_context(current_user) and not patient_id:
        return redirect(url_for("index"))
    cid = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
    session["conversation_id"] = cid
    session["id"] = cid
    session["conv"] = []
    return redirect(url_for("index"))


@misc_bp.post("/tts")
@csrf.exempt
@login_required
def tts_endpoint():
    """
    Google Cloud TTS endpoint.
    Expects JSON: {"text": "...", "role": "clinician"|..., "lang": "en"|"sw"|"auto"}
    Returns: {"audio_base64": "...", "content_type": "audio/wav"}
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    role = (data.get("role") or "clinician").strip().lower()
    lang = (data.get("lang") or "auto").strip().lower()  # en|sw|auto

    if not text:
        return jsonify({"error": "text is required"}), 400

    try:
        audio_path = synthesize_speech_open(text=text, out_format="wav", lang=lang, role=role)
    except Exception as e:
        logger.exception("TTS generation failed")
        return jsonify({"error": f"TTS generation failed: {e}"}), 500

    try:
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        b64 = base64.b64encode(audio_bytes).decode("ascii")
    except Exception as read_err:
        logger.exception("Failed to read TTS audio file")
        return jsonify({"error": f"Failed to read audio file: {read_err}"}), 500
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass

    return jsonify({
        "audio_base64": b64,
        "content_type": "audio/wav",
    })


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        return render_template('index.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500


@misc_bp.get('/clinicians', endpoint="clinicians")
@login_required
def clinician_page():
    if not any(r.name == "admin" for r in current_user.roles):
        return "Forbidden", 403
    db = SessionLocal()
    try:
        admins = (
            db.query(User)
            .join(User.roles)
            .filter(Role.name == "admin")
            .order_by(desc(User.created_at))
            .all()
        )
        clinicians = (
            db.query(User)
            .join(User.roles)
            .filter(Role.name == "clinician")
            .filter(~User.roles.any(Role.name == "admin"))
            .order_by(desc(User.created_at))
            .all()
        )
        return render_template(
            'clinicians.html',
            clinicians=clinicians,
            admins=admins,
            institutions=db.query(Institution).all(),
        )
    finally:
        db.close()

@misc_bp.get('/clinician_dashboard', endpoint='clinician_dashboard')
@login_required
def clinician_dashboard():
    if not any(r.name == "clinician" for r in current_user.roles):
        return "Forbidden", 403
    return render_template('clinician_dashboard.html')

@misc_bp.get('/new-password', endpoint="new-password")
@login_required
def set_password():
    # Redirect anyone who isn't a clinician OR a clinician who doesn't need a reset
    if not any(r.name == "clinician" for r in current_user.roles) or not current_user.reset_password:
        return redirect(url_for('index')) 

    # Only clinicians needing reset reach here
    return render_template('setpassword.html')


@misc_bp.get('/reset-password', endpoint="reset-password")
def reset_password():
    return render_template('resetpassword.html')


@misc_bp.get('/otp-verification', endpoint="otp-verification")
def otp_after_signup():
    # Get the email from session or URL param
    email = request.args.get('email')
    if not email:
        return redirect(url_for('index'))

    db = SessionLocal()
    try:
        # Get the user
        user = db.query(User).filter_by(email=email).first()
        if not user:
            return redirect(url_for('index'))

        # Check if user has "patient" role via user_roles join
        patient_role = db.query(Role).filter_by(name="patient").first()
        has_patient_role = db.query(user_roles).filter_by(user_id=user.id, role_id=patient_role.id).first() if patient_role else False

        # Only allow if patient and not yet verified
        if not has_patient_role or user.email_verified:
            return redirect(url_for('index'))

        return render_template('signup_otp.html', email=email)
    finally:
        db.close()


@misc_bp.get('/reset-email', endpoint="reset-email")
def reset_email():
    return render_template('reset_email.html')