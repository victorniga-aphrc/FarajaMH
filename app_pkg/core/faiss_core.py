# app_pkg/core/faiss_core.py
from __future__ import annotations
import json, logging, os, re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from mental_health_faiss import MentalHealthQuestionsFAISS  # your FAISS wrapper
except Exception as e:
    MentalHealthQuestionsFAISS = None
    logger.info("FAISS class not available: %s", e)

# shared handle: either a FAISS engine or the JSON adapter below
faiss_system: Optional[Any] = None


# ---------- JSON fallback adapter ----------
@dataclass
class _QItem:
    id: str
    english: str
    swahili: str
    category: str
    tags: List[str]

class JSONQuestionsAdapter:
    def __init__(self, items: List[_QItem]):
        self._items = items
        self.questions = items  # keep old attribute name for compatibility

    @classmethod
    def from_json(cls, path: str) -> "JSONQuestionsAdapter":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = []
        for r in raw:
            q = r.get("question") or {}
            items.append(
                _QItem(
                    id=r.get("id","").strip(),
                    english=(q.get("english") or "").strip(),
                    swahili=(q.get("swahili") or "").strip(),
                    category=(r.get("category") or "").strip(),
                    tags=list(r.get("tags") or []),
                )
            )
        return cls(items)

    def search(self, query: str, category: Optional[str] = None, k: int = 50) -> List[Dict[str, Any]]:
        q = (query or "").strip().lower()
        cat = (category or "").strip().lower()
        words = [w for w in re.split(r"\W+", q) if w]

        scored = []
        for it in self._items:
            if cat and it.category.lower() != cat:
                continue
            text = f"{it.id} {it.category} {it.english} {it.swahili} {' '.join(it.tags)}".lower()
            if not q:
                score = 1  # category-only browse
            else:
                score = sum(text.count(w) for w in words)
                if score == 0:
                    continue
            scored.append((score, it))

        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for _, it in scored[: max(1, k)]:
            out.append({
                "id": it.id,
                "english": it.english,
                "swahili": it.swahili,
                "category": it.category,
                "tags": it.tags,
                "score": _,
            })
        return out

    def list(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        cat = (category or "").strip().lower()
        out = []
        for it in self._items:
            if cat and it.category.lower() != cat:
                continue
            out.append({
                "id": it.id,
                "english": it.english,
                "swahili": it.swahili,
                "category": it.category,
                "tags": it.tags,
            })
        return out

    def meta(self) -> Dict[str, Any]:
        cats = {}
        for it in self._items:
            cats[it.category] = cats.get(it.category, 0) + 1
        return {"engine": "json", "total": len(self._items), "by_category": cats}


# ---------- Initialization ----------
def _project_root() -> Path:
    here = Path(__file__).resolve()           # .../app_pkg/core/faiss_core.py
    return here.parent.parent.parent          # .../Proper_Diagnosis

def initialize_faiss(app) -> bool:
    """
    Try FAISS first; if unavailable, fall back to JSON adapter.
    Sets global faiss_system to whichever backend is active.
    """
    global faiss_system
    faiss_system = None

    root = _project_root()
    # ---- resolve configured paths (env > app.config > fallback) ----
    index_fallback = root / "data" / "faiss" / "mental_health_cases.index"
    meta_fallback  = root / "data" / "faiss" / "mental_health_cases_metadata.pkl"  # keep your original pkl
    json_fallback  = root / "data" / "faiss" / "questions.json"

    idx = (os.getenv("FAISS_INDEX_PATH")
           or app.config.get("FAISS_INDEX_PATH")
           or str(index_fallback))
    mta = (os.getenv("FAISS_METADATA_PATH")
           or app.config.get("FAISS_METADATA_PATH")
           or str(meta_fallback))
    qjson = (os.getenv("QUESTIONS_JSON_PATH")
             or app.config.get("QUESTIONS_JSON_PATH")
             or str(json_fallback))

    # ---- 1) Try FAISS ----
    if MentalHealthQuestionsFAISS:
        try:
            if Path(idx).exists() and Path(mta).exists():
                eng = MentalHealthQuestionsFAISS()
                eng.load_index(idx, mta)
                faiss_system = eng
                logger.info("FAISS loaded OK (index=%s, meta=%s).", idx, mta)
                return True
            else:
                logger.info("FAISS files missing (index exists=%s, meta exists=%s).",
                            Path(idx).exists(), Path(mta).exists())
        except Exception:
            logger.exception("FAISS load failed; will try JSON fallback.")

    # ---- 2) JSON fallback ----
    try:
        if Path(qjson).exists():
            faiss_system = JSONQuestionsAdapter.from_json(qjson)
            logger.info("Using JSON questions fallback at %s (n=%d).",
                        qjson, len(getattr(faiss_system, "questions", [])))
            return True
        else:
            logger.error("JSON questions file missing: %s", qjson)
            return False
    except Exception:
        logger.exception("Failed to load JSON questions fallback.")
        return False
