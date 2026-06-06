"""Persistence layer — remembers completed analyses across refreshes/redeploys.

Uses DATABASE_URL when set (Neon Postgres in production, per the course Day-2
slides). Falls back to a local SQLite file when it isn't, so the app still runs
with zero setup during local development.

Storage-by-shape (slides' principle): the bulky result blob (UTCI grid + base64
heatmap PNGs + GeoJSON) lives in the `results` JSON column; small summary fields
are denormalised into their own columns so the list endpoint stays light.
"""
import math
import os
from datetime import datetime

from sqlalchemy import (
    create_engine, String, DateTime, JSON, Float, Integer, select,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column

# ── Engine ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# Neon/Heroku sometimes hand out the legacy "postgres://" scheme; SQLAlchemy
# needs "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///" + os.path.join(os.path.dirname(__file__), "treeat.db")

# Analyses finish on background threads, so SQLite must allow cross-thread use.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

Base = declarative_base()


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String, primary_key=True)          # == job_id
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    label: Mapped[str] = mapped_column(String, default="")
    polygon: Mapped[dict] = mapped_column(JSON)
    results: Mapped[dict] = mapped_column(JSON)
    # Denormalised summary (for the list view — avoids loading the big blob)
    utci_mean: Mapped[float] = mapped_column(Float, default=0.0)
    utci_after_mean: Mapped[float] = mapped_column(Float, default=0.0)
    total_trees: Mapped[int] = mapped_column(Integer, default=0)
    n_planting_streets: Mapped[int] = mapped_column(Integer, default=0)


Base.metadata.create_all(engine)


def _f(v, default=0.0):
    """Coerce to a finite float — NaN/inf become the default (DBs reject NaN)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _clean(obj):
    """Recursively replace non-finite floats (NaN/inf) with None so the value
    is valid JSON for both SQLite and Postgres (the UTCI grid has NaN cells)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def save_analysis(job_id: str, polygon: dict, results: dict, label: str = "") -> None:
    """Insert (or replace) a completed analysis. Safe to call from a thread."""
    stats = (results or {}).get("stats", {}) or {}
    row = Analysis(
        id=job_id,
        created_at=datetime.utcnow(),
        label=label,
        polygon=_clean(polygon),
        results=_clean(results),
        utci_mean=_f(stats.get("utci_mean")),
        utci_after_mean=_f(stats.get("utci_after_mean")),
        total_trees=int(_f(stats.get("total_trees"))),
        n_planting_streets=int(_f(stats.get("n_planting_streets"))),
    )
    with SessionLocal() as s:
        s.merge(row)   # merge = upsert by primary key
        s.commit()


def list_analyses() -> list[dict]:
    """Lightweight list for the sidebar — no big result blob."""
    with SessionLocal() as s:
        rows = s.execute(
            select(Analysis).order_by(Analysis.created_at.desc())
        ).scalars().all()
        return [{
            "id": r.id,
            "created_at": r.created_at.isoformat() + "Z",
            "label": r.label,
            "utci_mean": r.utci_mean,
            "utci_after_mean": r.utci_after_mean,
            "total_trees": r.total_trees,
            "n_planting_streets": r.n_planting_streets,
        } for r in rows]


def get_analysis(aid: str) -> dict | None:
    """Full saved record, including the result blob needed to redraw the map."""
    with SessionLocal() as s:
        r = s.get(Analysis, aid)
        if not r:
            return None
        return {
            "id": r.id,
            "created_at": r.created_at.isoformat() + "Z",
            "label": r.label,
            "polygon": r.polygon,
            "results": r.results,
        }


def delete_analysis(aid: str) -> bool:
    with SessionLocal() as s:
        r = s.get(Analysis, aid)
        if not r:
            return False
        s.delete(r)
        s.commit()
        return True


def diagnostics() -> dict:
    """Self-test the DB write path and report config — for debugging only."""
    import traceback
    backend = "postgres" if DATABASE_URL.startswith("postgresql") else (
        "sqlite" if DATABASE_URL.startswith("sqlite") else "other")
    info = {"backend": backend, "url_scheme": DATABASE_URL.split("://", 1)[0],
            "database_url_set": bool(os.getenv("DATABASE_URL", "").strip())}
    if backend == "sqlite":
        info["sqlite_path"] = DATABASE_URL.replace("sqlite:///", "")
        info["dir_writable"] = os.access(os.path.dirname(info["sqlite_path"]) or ".", os.W_OK)
    try:
        poly = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
        res = {"grid": [[1.0, float("nan")]], "stats": {"utci_mean": float("nan"),
               "utci_after_mean": 1.0, "total_trees": 1, "n_planting_streets": 1}}
        save_analysis("__selftest__", poly, res, "selftest")
        ok = any(r["id"] == "__selftest__" for r in list_analyses())
        delete_analysis("__selftest__")
        info["write_ok"] = ok
        info["error"] = None
    except Exception as e:
        info["write_ok"] = False
        info["error"] = f"{type(e).__name__}: {e}"
        info["traceback"] = traceback.format_exc()[-1500:]
    return info
