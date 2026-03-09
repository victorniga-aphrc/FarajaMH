from __future__ import annotations

import csv
import io
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, send_file, session
from flask_login import current_user, login_required

from .. import csrf
from ..core import faiss_core
from models import Message, SessionLocal, create_conversation, log_message

faiss_bp = Blueprint("faiss_bp", __name__)

# Keeps track of the last recommended FAISS question per conversation so that
# a subsequent user answer can be linked back to the recommended question.
_PENDING_FAISS_Q: dict[str, tuple[str | None, str | None]] = {}


def _set_pending_faiss_q(cid: str, qid: str | None, cat: str | None):
    if cid:
        _PENDING_FAISS_Q[cid] = (qid, cat)


def _pop_pending_faiss_q(cid: str) -> tuple[str | None, str | None]:
    return _PENDING_FAISS_Q.pop(cid, (None, None))


def _ensure_qfaiss():
    # Dynamic reference (do NOT cache at import time).
    return faiss_core.faiss_system


def _ensure_conversation_id() -> str:
    cid = session.get("conversation_id") or session.get("id")
    if not cid:
        roles = {getattr(r, "name", "").lower() for r in getattr(current_user, "roles", [])}
        patient_id = session.get("active_patient_id")
        if (("clinician" in roles) or ("admin" in roles)) and not patient_id:
            raise ValueError("Select a patient before starting a conversation")
        cid = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
    session["conversation_id"] = cid
    session["id"] = cid
    session.setdefault("conv", [])
    return cid


def _norm_cat(c):
    c = (c or "").strip().lower()
    return c if c in ("depression", "anxiety", "psychosis") else None


def _as_question_item(it):
    """Normalize a question item to a single dict shape.

    Supports:
      - dict items: {'id','category','question':{'english','swahili'}, ...}
      - str items: 'some question text' (legacy)
    """
    if isinstance(it, str):
        return {
            "id": None,
            "category": None,
            "question": {"english": it, "swahili": ""},
            "tags": [],
        }
    if isinstance(it, dict):
        q = it.get("question") or {}
        if not isinstance(q, dict):
            q = {}
        return {
            "id": it.get("id"),
            "category": (it.get("category") or "").strip().lower() or None,
            "question": {"english": q.get("english") or "", "swahili": q.get("swahili") or ""},
            "tags": it.get("tags") or [],
        }
    return {"id": None, "category": None, "question": {"english": "", "swahili": ""}, "tags": []}


@faiss_bp.post("/faiss/suggest_question")
@csrf.exempt
@login_required
def faiss_suggest_question():
    f = _ensure_qfaiss()
    if not f:
        return jsonify({"error": "FAISS not loaded"}), 503

    data = request.get_json(silent=True) or {}
    query_text = (data.get("text") or "").strip()
    k = int(data.get("k", 1))

    if not query_text:
        return jsonify({"error": "text is required"}), 400

    # NOTE: 0.38 was too strict for short / code-switched user text.
    results = f.suggest_questions(query_text, k=max(1, k), threshold=0.25) or []
    if not results:
        return jsonify({"question": None, "reason": "no_match"}), 200

    q = results[0]

    # Ensure conversation exists
    try:
        sid = _ensure_conversation_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    eng_text = (q.get("question", {}) or {}).get("english") or ""
    try:
        log_message(
            sid,
            role="question_recommender",
            message=eng_text.strip(),
            timestamp=datetime.utcnow().isoformat(timespec="seconds"),
            type_="question_recommender",
            faiss_question_id=q.get("id"),
            faiss_category=q.get("category"),
            faiss_is_answer=False,
        )
    except Exception:
        pass

    _set_pending_faiss_q(sid, q.get("id"), q.get("category"))
    payload = {
        "question": q.get("question"),
        "id": q.get("id"),
        "category": q.get("category"),
        "similarity": q.get("similarity"),
    }
    return jsonify(payload), 200


@faiss_bp.post("/faiss/mark_answer")
@csrf.exempt
@login_required
def faiss_mark_answer():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    try:
        sid = _ensure_conversation_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    qid, qcat = _pop_pending_faiss_q(sid)

    try:
        log_message(
            sid,
            role="patient",
            message=text,
            timestamp=datetime.utcnow().isoformat(timespec="seconds"),
            type_="message",
            faiss_question_id=qid,
            faiss_category=qcat,
            faiss_is_answer=bool(qid),
        )
    except Exception:
        pass

    return (
        jsonify({"ok": True, "linked_to_faiss": bool(qid), "faiss_question_id": qid, "faiss_category": qcat}),
        200,
    )


@faiss_bp.post("/faiss/search")
@csrf.exempt
@login_required
def search():
    """Unified search endpoint used by the UI search box.

    Behavior:
      - If query is empty: list questions (optionally filtered by category)
      - If query is provided: do FAISS similarity suggestions
    """
    try:
        fs = faiss_core.faiss_system  # dynamic read
        if not fs:
            return jsonify({"error": "FAISS not loaded"}), 503

        data = request.get_json(silent=True) or {}
        query = (data.get("query") or "").strip()
        cat = _norm_cat(data.get("category"))
        # Cap results to keep responses small and predictable.
        # NOTE: a previous edit accidentally did: int(min(...), 50) (base=50)
        # which raises TypeError and causes a 500.
        raw_k = data.get("k", data.get("max_results", 25))
        try:
            raw_k = int(raw_k)
        except Exception:
            raw_k = 25
        k = min(max(raw_k, 1), 50)
        # Lower default threshold; allow UI to override.
        similarity_threshold = float(data.get("similarity_threshold", 0.22))

        # ✅ If no query text, return a (category-filtered) list instead of "no results"
        if not query:
            questions = getattr(fs, "questions", []) or []
            out_q = []
            for it in questions:
                q = _as_question_item(it)
                if cat and q["category"] != cat:
                    continue
                out_q.append(
                    {
                        "question_id": q["id"],
                        "question": q["question"],
                        "category": q["category"],
                        "similarity": None,
                        "tags": q["tags"],
                    }
                )
                if len(out_q) >= k:
                    break

            return jsonify({"query": query, "results": [], "suggested_questions": out_q, "total_results": len(out_q)}), 200

        # Similarity suggestion search (questions)
        if hasattr(fs, "suggest_questions"):
            hits = fs.suggest_questions(query, k=k, threshold=similarity_threshold) or []
            out_q = [
                {
                    "question_id": h.get("id"),
                    "question": h.get("question"),
                    "category": (h.get("category") or "").strip().lower() or None,
                    "similarity": float(h.get("similarity", 0.0)),
                    "tags": h.get("tags", []),
                }
                for h in hits
                if (not cat) or ((h.get("category") or "").strip().lower() == cat)
            ]
            return jsonify({"query": query, "results": [], "suggested_questions": out_q, "total_results": len(out_q)}), 200

        # If this instance is a cases-index wrapper (unexpected for this app), return a helpful error.
        return jsonify({"error": "Search backend not available"}), 503

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@faiss_bp.get("/questions/meta")
@login_required
def questions_meta():
    try:
        f = _ensure_qfaiss()
        if not f:
            return jsonify({"error": "FAISS not loaded"}), 503
        cats = {"depression": 0, "anxiety": 0, "psychosis": 0, "other": 0}
        for q in getattr(f, "questions", []) or []:
            if not isinstance(q, dict):
                cats["other"] += 1
                continue
            cat = (q.get("category") or "").lower()
            if cat in cats:
                cats[cat] += 1
            else:
                cats["other"] += 1
        return jsonify({"categories": cats, "total": len(getattr(f, "questions", []) or [])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@faiss_bp.get("/questions/list")
@login_required
def questions_list():
    try:
        f = _ensure_qfaiss()
        if not f:
            return jsonify({"error": "FAISS not loaded"}), 503

        cat = _norm_cat(request.args.get("category"))
        qtext = (request.args.get("q") or "").strip().lower()
        items = []
        for it in getattr(f, "questions", []) or []:
            q = _as_question_item(it)
            c = q["category"]
            if cat and c != cat:
                continue
            en = q["question"]["english"]
            sw = q["question"]["swahili"]
            blob = (en + " " + sw).lower()
            if qtext and qtext not in blob:
                continue
            items.append(
                {"id": q["id"], "category": c, "english": en, "swahili": sw, "tags": q["tags"]}
            )
        return jsonify({"count": len(items), "items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@faiss_bp.post("/questions/search")
@csrf.exempt
@login_required
def questions_search():
    try:
        f = _ensure_qfaiss()
        if not f:
            return (
                jsonify(
                    {
                        "error": "FAISS not loaded",
                        "hint": "Check FAISS_INDEX_PATH / FAISS_METADATA_PATH or data/faiss fallback",
                    }
                ),
                503,
            )

        data = request.get_json(force=True) or {}
        query = (data.get("query") or "").strip()
        cat = _norm_cat(data.get("category"))
        k = int(data.get("k", 25))

        if not query:
            return jsonify({"count": 0, "items": []})

        # More forgiving threshold
        hits = f.suggest_questions(query, k=k, threshold=float(data.get("threshold", 0.22))) or []

        def _norm(h):
            if isinstance(h, dict):
                item = h.get("item") or h.get("data") or h
                q = item.get("question") if isinstance(item, dict) else {}
                return {
                    "id": item.get("id") or h.get("id"),
                    "category": (item.get("category") or h.get("category") or "").lower() or None,
                    "english": (q or {}).get("english") or h.get("question", {}).get("english") or "",
                    "swahili": (q or {}).get("swahili") or h.get("question", {}).get("swahili") or "",
                    "similarity": float(h.get("similarity") or item.get("similarity") or 0.0),
                    "tags": item.get("tags") or h.get("tags") or [],
                }
            q = getattr(h, "question", {}) or {}
            return {
                "id": getattr(h, "question_id", None),
                "category": (getattr(h, "category", "") or "").lower() or None,
                "english": (q.get("english") if isinstance(q, dict) else "") or "",
                "swahili": (q.get("swahili") if isinstance(q, dict) else "") or "",
                "similarity": float(getattr(h, "similarity_score", 0.0) or 0.0),
                "tags": list(getattr(h, "tags", []) or []),
            }

        out = []
        for h in hits:
            row = _norm(h)
            if cat and row["category"] != cat:
                continue
            out.append(row)

        return jsonify({"count": len(out), "items": out})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@faiss_bp.get("/questions/print")
@login_required
def questions_print():
    try:
        f = _ensure_qfaiss()
        if not f:
            return Response("<pre>Error: FAISS not loaded</pre>", mimetype="text/html", status=503)

        cat = _norm_cat(request.args.get("category"))
        items = []
        for it in getattr(f, "questions", []) or []:
            q = _as_question_item(it)
            c = q["category"]
            if cat and c != cat:
                continue
            items.append(
                {
                    "id": q["id"],
                    "category": c or "",
                    "english": q["question"]["english"],
                    "swahili": q["question"]["swahili"],
                }
            )

        html = [
            "<html><head><meta charset='utf-8'><title>Question Bank</title>",
            "<style>body{font-family:sans-serif} .q{margin:10px 0;padding:8px;border-bottom:1px solid #ddd}</style>",
            "</head><body>",
        ]
        html.append(f"<h2>Question Bank{(' — ' + cat.capitalize()) if cat else ''}</h2>")
        html.append("<p><em>English and Swahili</em></p>")
        for x in items:
            html.append(
                f"<div class='q'><div><strong>{x['id']}</strong> · "
                f"<span style='color:#555'>{x['category']}</span></div>"
                f"<div><strong>English:</strong> {x['english']}</div>"
                f"<div><strong>Swahili:</strong> {x['swahili']}</div></div>"
            )
        html.append("<script>window.print()</script></body></html>")
        return Response("\n".join(html), mimetype="text/html")
    except Exception as e:
        return Response(f"<pre>Error: {e}</pre>", mimetype="text/html", status=500)


@faiss_bp.get("/questions/export")
@login_required
def questions_export():
    try:
        f = _ensure_qfaiss()
        if not f:
            return jsonify({"error": "FAISS not loaded"}), 503

        cat = _norm_cat(request.args.get("category"))
        qtext = (request.args.get("q") or "").strip().lower()

        output = io.StringIO()
        w = csv.writer(output)
        w.writerow(["id", "category", "english", "swahili", "tags"])

        for it in getattr(f, "questions", []) or []:
            q = _as_question_item(it)
            c = q["category"]
            if cat and c != cat:
                continue
            en = q["question"]["english"]
            sw = q["question"]["swahili"]
            blob = (en + " " + sw).lower()
            if qtext and qtext not in blob:
                continue
            w.writerow([q["id"], c or "", en, sw, " ".join(q["tags"])])

        mem = io.BytesIO(output.getvalue().encode("utf-8"))
        filename = f"questions{('-' + cat) if cat else ''}.csv"
        return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@faiss_bp.get("/case/<case_id>")
@login_required
def get_case_details(case_id):
    try:
        if hasattr(faiss_core.faiss_system, "get_case_details"):
            case_details = faiss_core.faiss_system.get_case_details(case_id)
            if case_details:
                return jsonify(case_details)
            return jsonify({"error": "Case not found"}), 404
        return jsonify({"error": "Cases index not available"}), 404
    except Exception:
        return jsonify({"error": "An error occurred"}), 500


@faiss_bp.get("/admin/api/faiss_answered_summary")
@login_required
def admin_faiss_answered_summary():
    if not any(r.name == "admin" for r in current_user.roles):
        return "Forbidden", 403

    db = SessionLocal()
    try:
        from sqlalchemy import func

        rows = (
            db.query(Message.faiss_category, func.count())
            .filter(Message.faiss_is_answer.is_(True))
            .group_by(Message.faiss_category)
            .all()
        )
        counts = {"depression": 0, "anxiety": 0, "psychosis": 0}
        for cat, c in rows:
            if cat in counts:
                counts[cat] = int(c)
        total = sum(counts.values()) or 1
        pct = {k: round(100.0 * v / total, 1) for k, v in counts.items()}
        return jsonify({"counts": counts, "percents": pct})
    finally:
        db.close()
