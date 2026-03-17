# admin.py
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, desc, or_
from collections import Counter, defaultdict
import re
import string, random
from security import hash_password
from auth import grant_role
from models import (
    SessionLocal,
    Conversation,
    Message,
    User,
    Role,
    user_roles,
    Institution,
    delete_conversation_by_id,
)
from send_email import send_mail_with_html_file
# Reuse the same screening logic used by /mh/screen
from screening import run_screening, screening_to_dict

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# --------------------------
# Auth guards
# --------------------------
def _require_admin():
    return current_user.is_authenticated and any(r.name == "admin" for r in current_user.roles)

def _require_admin_clinician():
    return current_user.is_authenticated and any(r.name == "clinician" or r.name == "admin" for r in current_user.roles)

def admin_guard():
    if not _require_admin():
        return jsonify({"ok": False, "error": "Admin only"}), 403

def admin_clinician_guard():
    if not _require_admin_clinician():
        return jsonify({"ok": False, "error": "Admin and Clinician only"}), 403


# --------------------------
# Helpers: text cleaning & symptom extraction
# --------------------------
# For pulling a single target symptom from recommender text (your existing heuristic)

SYM_RE = re.compile(
    r"(?:symptom|target|focus)\s*:\s*([A-Za-z][\w\s/-]{1,80}?)",
    re.IGNORECASE
)
TAG_RE = re.compile(r"<[^>]+>")

def _safe_text(m: Message) -> str:
    msg = getattr(m, "message", "") or ""
    return TAG_RE.sub("", msg)

# Counter-based symptom extraction for tallies/graphs
SYMPTOM_LEXICON = [
    "Sadness", "Loss of interest", "Irritability", "Hopelessness", "Guilt", "Mood swings",
    "Worry", "Panic attacks", "Restlessness", "Muscle tension", "Avoidance", "Phobias", 
    "Poor concentration", "Memory problems", "Racing thoughts", "Indecisiveness", "Rumination",
    "Intrusive thoughts", "Hallucinations", "Delusions", "Disorganized thinking", "Derealization",
    "Depersonalization", "Social withdrawal", "Sleep changes", "Appetite changes", "Reckless behavior",
    "Aggression", "Self-harm", "Fatigue", "Headaches", "Gastrointestinal issues", "Palpitations",
    "Weight changes", "Substance misuse", "Obsessions", "Compulsions", "Impaired functioning", "Emotional numbness",
    "Anxious", "Depressed", "Sleepless nights","lost interest","no interest","lack of pleasure","nothing is enjoyable",
    "feeling down","sad","depressed","blue","hopeless","helpless", "trouble sleeping","insomnia","sleeping too much",
    "tired","fatigue","exhausted","drained","no energy","poor appetite","overeating","weight loss","weight gain",
    "worthless","guilty","failure","self blame","ashamed", "trouble concentrating","hard to focus","mind is slow",
    "moving slowly","slowed down","restless","fidgety", "suicidal","suicide","want to die","kill myself","end my life",
    "self harm","self-harm","hurt myself","harm myself","cut myself", "crying a lot","tearful","weeping","breaking down",
    "avoiding people","don’t want to see anyone","isolated","feel like a failure","not good enough","I am useless",
    "worry a lot","cannot control worry","always worried","overthinking", "on edge","restless","uneasy","fidgety","nervous",
    "mind goes blank","hard to focus","cannot concentrate", "irritable","short tempered","easily annoyed","snappy",
    "muscle tension","tense","tight muscles","jaw clenching", "trouble sleeping","difficulty staying asleep",
    "heart racing","palpitations","short of breath","chest tightness", "panic attack","suffocating", 
    "stomach upset","nausea","sweating","shaking","trembling", "fear of leaving home","afraid of crowds","fear of public places",
    "hearing voices","voices talking","voices commenting", "seeing things","visions","shadows moving",
    "out to get me","people following me","being spied on","poisoned", "messages for me","TV talking to me","radio sending messages",
    "thoughts mixed up","cannot think straight","speech disorganized", "strange behavior","acting odd","weird actions",
    "people are watching me","they put cameras","spying on me", "lack of motivation","flat affect","emotionless","not talking",
    "staring blankly","not moving","frozen","repetitive movements",
]
CANON = {s: s for s in SYMPTOM_LEXICON}
CANON.update({
    "sob": "shortness of breath",
    "dyspnea": "shortness of breath",
    "tiredness": "fatigue",
    "lightheadedness": "dizziness",
    "chest tightness": "chest pain",
    "loose stools": "diarrhea",
    "constipated": "constipation",
    "weightloss": "weight loss",
})

def extract_symptoms(text: str) -> Counter:
    t = " " + (text or "").lower() + " "
    t = t.translate(str.maketrans(string.punctuation, " "*len(string.punctuation)))
    counts = Counter()
    # phrase-first to catch multi-word entries
    for phrase in sorted(CANON.keys(), key=len, reverse=True):
        pattern = r'\b' + re.escape(phrase.lower()) + r'\b'
        hits = re.findall(pattern, t, re.IGNORECASE)  # <-- ignore case
        if hits:
            counts[CANON[phrase]] += len(hits)
            t = re.sub(pattern, " ", t, flags=re.IGNORECASE)

    return counts


def _display_name(username: str | None, name: str | None, email: str | None) -> str:
    if username:
        return username
    if name:
        return name
    return (email or "").split("@")[0] if email else ""


def _generate_unique_username(db, base: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_]+", "_", (base or "").strip().lower()).strip("_") or "user"
    if not db.query(User).filter(User.username == candidate).first():
        return candidate
    i = 2
    while True:
        c = f"{candidate}{i}"
        if not db.query(User).filter(User.username == c).first():
            return c
        i += 1

# --------------------------
# Overview stats
# --------------------------
@admin_bp.get("/api/summary")
@login_required
def summary():
    if not _require_admin_clinician():
        return admin_clinician_guard()

    db = SessionLocal()
    try:
        is_clinician = any(r.name == "clinician" for r in current_user.roles)

        # -------------------------
        # USERS (admins always see full counts)
        # -------------------------
        total_users = db.query(User).count()

        clinicians = (
            db.query(User)
              .join(user_roles, user_roles.c.user_id == User.id)
              .join(Role, Role.id == user_roles.c.role_id)
              .filter(Role.name == "clinician")
              .count()
        )

        admins = (
            db.query(User)
              .join(user_roles, user_roles.c.user_id == User.id)
              .join(Role, Role.id == user_roles.c.role_id)
              .filter(Role.name == "admin")
              .count()
        )

        # -------------------------
        # CONVERSATIONS (scoped)
        # -------------------------
        conv_q = db.query(Conversation)

        if is_clinician:
            conv_q = conv_q.filter(
                Conversation.owner_user_id == current_user.id
            )

        total_convos = conv_q.count()

        conv_ids_subq = conv_q.with_entities(Conversation.id).subquery()

        # -------------------------
        # MESSAGES (derived from conversations)
        # -------------------------
        msg_q = db.query(Message)

        if is_clinician:
            msg_q = msg_q.filter(
                Message.conversation_id.in_(conv_ids_subq)
            )

        total_messages = msg_q.count()

        rec_questions = msg_q.filter(
            Message.type == "question_recommender"
        ).count()

        # -------------------------
        # CONVERSATIONS PER DAY
        # -------------------------
        convs_per_day_q = (
            db.query(
                func.date(Conversation.created_at),
                func.count(Conversation.id)
            )
        )

        if is_clinician:
            convs_per_day_q = convs_per_day_q.filter(
                Conversation.owner_user_id == current_user.id
            )

        convs_per_day = (
            convs_per_day_q
            .group_by(func.date(Conversation.created_at))
            .order_by(func.date(Conversation.created_at))
            .limit(30)
            .all()
        )


        top_clinicians = []

        if not is_clinician:
            # ADMIN ONLY
            top_clinicians = (
                db.query(
                    User.email,
                    User.username,
                    User.name,
                    func.count(Conversation.id).label("count")
                )
                .join(Conversation, Conversation.owner_user_id == User.id)
                .join(user_roles, user_roles.c.user_id == User.id)
                .join(Role, Role.id == user_roles.c.role_id)
                .filter(Role.name == "clinician")
                .group_by(User.email, User.username, User.name)
                .order_by(desc("count"))
                .limit(10)
                .all()
            )

        return jsonify({
            "ok": True,
            "users": {
                "total": total_users,
                "clinicians": clinicians,
                "admins": admins
            },
            "conversations": {
                "total": total_convos
            },
            "messages": {
                "total": total_messages,
                "recommended": rec_questions
            },
            "series": {
                "conversations_per_day": [
                    [str(d), c] for d, c in convs_per_day
                ],
                "top_clinicians": [
                {"email": email, "display_name": _display_name(username, name, email), "count": count}
                for email, username, name, count in top_clinicians
                ]
            },
        })

    finally:
        db.close()



# --------------------------
# Paginated conversations (includes owner email)
# --------------------------
# --- Paginated conversations (owner via conversations.owner_user_id) ---
def _is_admin(user):
    return any(r.name == "admin" for r in user.roles)

def _is_clinician(user):
    return any(r.name == "clinician" for r in user.roles)


def _conversation_allowed_for_current_user(db, cid: str):
    convo = db.query(Conversation).filter(Conversation.id == cid).first()
    if not convo:
        return None
    if _is_admin(current_user):
        return convo
    if _is_clinician(current_user) and convo.owner_user_id == current_user.id:
        return convo
    return None

@admin_bp.get("/api/conversations")
@login_required
def conversations():
    if not _require_admin_clinician():
        return admin_clinician_guard()

    page = int(request.args.get("page", 1))
    size = min(int(request.args.get("size", 20)), 100)
    offset = (page - 1) * size

    db = SessionLocal()
    try:
        # Base query
        base_query = (
            db.query(
                Conversation.id,
                Conversation.created_at,
                User.email,
                User.username,
                User.name,
                Conversation.owner_user_id
            )
            .outerjoin(User, User.id == Conversation.owner_user_id)
        )

        # ROLE FILTER
        if _is_clinician(current_user):
            base_query = base_query.filter(
                Conversation.owner_user_id == current_user.id
            )

        total = base_query.count()

        rows = (
            base_query
            .order_by(Conversation.created_at.desc())
            .offset(offset)
            .limit(size)
            .all()
        )

        convs = [{
            "id": cid,
            "created_at": created.isoformat(),
            "owner": (username or name or email or (str(owner_id) if owner_id else None)),
            "owner_email": email,
            "owner_display_name": (username or name or (email.split("@")[0] if email else None)),
            "owner_user_id": owner_id,
        } for (cid, created, email, username, name, owner_id) in rows]

        return jsonify({
            "ok": True,
            "page": page,
            "size": size,
            "total": total,
            "conversations": convs
        })

    finally:
        db.close()



def extract_final_english_summary(text):
    """Return only the final English Summary block from a text."""
    if not text:
        return None

    match = re.search(
        r'(?:.*)(\*{0,2}English Summary:\*{0,2}.*?)(?=\*{0,2}Swahili Summary:|\Z)',
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if match:
        content = re.sub(r'^\*{0,2}English Summary:\*{0,2}', '', match.group(1), flags=re.IGNORECASE).strip()
        return content

    return None

#
# --------------------------
# Conversation detail (messages + recommended questions)
# --------------------------
@admin_bp.get("/api/conversation/<cid>")
@login_required
def conversation_detail(cid):
    if not _require_admin_clinician():
        return admin_clinician_guard()

    db = SessionLocal()
    try:
        allowed = _conversation_allowed_for_current_user(db, cid)
        if not allowed:
            return jsonify({"ok": False, "error": "Conversation not found"}), 404

        msgs = (
            db.query(Message)
              .filter(Message.conversation_id == cid)
              .order_by(Message.created_at.asc())
              .all()
        )

        out_msgs, recos = [], []
        for m in msgs:
            text = _safe_text(m)
            out_msgs.append({
                "id": m.id,
                "role": m.role,
                "type": m.type,
                "text": text,
                "timestamp": m.timestamp,
                "created_at": m.created_at.isoformat(),
            })
            if (m.type == "question_recommender") or (m.role == "Question Recommender"):
                recos.append({
                    "id": m.id,
                    "question": text,
                })


        return jsonify({"ok": True, "messages": out_msgs, "recommended_questions": recos})
    finally:
        db.close()

# --------------------------
# Symptom tallies (global + per-conversation)
# --------------------------
@admin_bp.get("/api/symptoms")
@login_required
def symptoms_api():
    if not _require_admin_clinician():
        return admin_clinician_guard()

    db = SessionLocal()
    try:
        # ---- Base conversation query ----
        convo_q = (
            db.query(
                Conversation.id,
                Conversation.created_at,
                User.email,
                Conversation.owner_user_id,
            )
            .outerjoin(User, User.id == Conversation.owner_user_id)
        )

        # ROLE FILTER
        if any(r.name == "clinician" for r in current_user.roles):
            convo_q = convo_q.filter(
                Conversation.owner_user_id == current_user.id
            )

        convo_rows = (
            convo_q
            .order_by(Conversation.created_at.desc())
            .all()
        )

        conv_ids = [cid for (cid, _created, _email, _uid) in convo_rows]

        # No conversations
        if not conv_ids:
            return jsonify({"ok": True, "global": {}, "by_conversation": []})

        
        msgs = (
                    db.query(Message)
                    .filter(Message.conversation_id.in_(conv_ids))
                    .order_by(Message.created_at.asc())
                    .all()
                )
        final_summary_by_conversation = {}

        for m in msgs:
            if m.role != "Clinician":
                continue

            text = _safe_text(m)
            summary = extract_final_english_summary(text)

            if summary:
                # overwrite → keeps the final summary for that conversation
                final_summary_by_conversation[m.conversation_id] = {
                    "message_id": m.id,
                    "timestamp": m.timestamp,
                    "english_summary": summary
                }


        from collections import Counter
        global_counts = Counter()

        for conv_id, data in final_summary_by_conversation.items():
            symptoms = extract_symptoms(data["english_summary"])
            global_counts.update(symptoms)

        return jsonify({
            "ok": True,
            "global": dict(global_counts.most_common()),
            "global_counts": global_counts,
            "total_convos": len(conv_ids)
            # "by_conversation": by_conv,
        })

    finally:
        db.close()



@admin_bp.get("/api/conversation/<cid>/disease_likelihoods")
@login_required
def conversation_disease_likelihoods(cid):
    if not _require_admin_clinician():
        return admin_clinician_guard()

    db = SessionLocal()
    try:
        allowed = _conversation_allowed_for_current_user(db, cid)
        if not allowed:
            return jsonify({"ok": False, "error": "Conversation not found"}), 404

        msgs = (
            db.query(Message)
              .filter(Message.conversation_id == cid)
              .order_by(Message.created_at.asc())
              .all()
        )
        if not msgs:
            return jsonify({"ok": False, "error": "No messages for conversation"}), 404

        # Prefer patient-only text; fall back to all text
        patient_text = " ".join((m.message or "") for m in msgs if (m.role or "").lower() == "patient").strip()
        if not patient_text:
            patient_text = " ".join((m.message or "") for m in msgs if m.message).strip()

        # Use the SAME model path as /mh/screen
        out = run_screening(transcript=patient_text, responses={}, safety_concerns=False)
        data = screening_to_dict(out)

        # Normalize to three percentages from confidences
        conf = {r["name"]: float(r.get("confidence", 0.0)) for r in data.get("results", [])}
        top_diseases = [
            {"disease": "depression", "likelihood_pct": round(conf.get("depression", 0.0) * 100, 1)},
            {"disease": "anxiety", "likelihood_pct": round(conf.get("anxiety", 0.0) * 100, 1)},
            {"disease": "psychosis", "likelihood_pct": round(conf.get("psychosis", 0.0) * 100, 1)},
        ]

        final_summary_by_conversation = {}

        for m in msgs:
            if m.role != "Clinician":
                continue

            text = _safe_text(m)
            summary = extract_final_english_summary(text)

            if summary:
                # overwrite → keeps the final summary for that conversation
                final_summary_by_conversation[m.conversation_id] = {
                    "message_id": m.id,
                    "timestamp": m.timestamp,
                    "english_summary": summary
                }

        from collections import Counter
        sym = Counter()

        for conv_id, data in final_summary_by_conversation.items():
            symptoms = extract_symptoms(data["english_summary"])
            sym.update(symptoms)

        return jsonify({
            "ok": True,
            "conversation_id": cid,
            "symptoms": dict(sym.most_common()),
            "top_diseases": top_diseases,
            "faiss_matches": [],  # optional: populate if you want to display matches
        })
    finally:
        db.close()


@admin_bp.delete("/api/conversation/<cid>")
@login_required
def delete_conversation(cid):
    if not _require_admin_clinician():
        return admin_clinician_guard()
    if _is_admin(current_user):
        ok = delete_conversation_by_id(cid)
    else:
        db = SessionLocal()
        try:
            convo = db.query(Conversation).filter(
                Conversation.id == cid,
                Conversation.owner_user_id == current_user.id,
            ).first()
            if not convo:
                return jsonify({"ok": False, "error": "Conversation not found"}), 404
            db.delete(convo)
            db.commit()
            ok = True
        finally:
            db.close()

    if not ok:
        return jsonify({"ok": False, "error": "Conversation not found"}), 404
    return jsonify({"ok": True, "conversation_id": cid})


def generate_temp_password(length=10):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(random.choice(chars) for _ in range(length))


@admin_bp.post("/api/clinicians/add")
@login_required
def add_clinician():
    if not _require_admin():
        return admin_guard()

    data = request.get_json(force=True) or request.form
    db = SessionLocal()
    try:
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        institution_id = data.get("institution_id") or None
        new_institution_name = data.get("new_institution") or None

        # Determine final institution name
        institution_name = new_institution_name.strip() if new_institution_name else None
        if not institution_name and institution_id:
            inst = db.query(Institution).filter_by(id=institution_id).first()
            if inst:
                institution_name = inst.name

        if not name or not email or not institution_name:
            return jsonify({"ok": False, "error": "All fields are required"}), 400

        # Check if user exists
        if db.query(User).filter_by(email=email).first():
            return jsonify({"ok": False, "error": "User Email already registered"}), 409

        # Find or create institution
        inst = db.query(Institution).filter(func.lower(Institution.name) == institution_name.lower()).first()
        if not inst:
            inst = Institution(name=institution_name)
            db.add(inst)
            db.commit()
            db.refresh(inst)

        # Create user with temp password
        temp_password = generate_temp_password()
        user = User(
            email=email,
            username=_generate_unique_username(db, name or email.split("@")[0]),
            password_hash=hash_password(temp_password),
            name=name,
            is_active=True,
            institution_id=inst.id,
            reset_password=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Assign clinician role
        grant_role(db, user, "clinician")

        status, response = send_mail_with_html_file(
        recipient_email=email,
        subject="Onboarding into MHS Application",
        html_file_name="email_template.html",
        placeholders={
            "message": "Kindly use this temporary password: " + temp_password}
        )

        return jsonify({
            "ok": True,
            "clinician": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "institution": inst.name,
            }
        })
    finally:
        db.close()
