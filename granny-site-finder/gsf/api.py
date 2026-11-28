"""FastAPI app: JSON API + the static web UI. Local use only (binds 127.0.0.1 by default)."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from . import feaso as fz
from .config import FEASO_KEYS, feaso_inputs, stress_spec
from .pipeline import Pipeline
from .report import assumptions, ranked, to_csv
from .sources.csv_source import CsvSource
from .sources.paste import PasteSource

WEB = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="Granny-Site Finder", docs_url="/api/docs")
_pipe: Pipeline | None = None
_jobs: dict[str, dict[str, Any]] = {}
_run_lock = threading.Lock()


def pipe() -> Pipeline:
    global _pipe
    if _pipe is None:
        _pipe = Pipeline()
    return _pipe


# ------------------------------------------------------------------ models --
class PasteIn(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class ListingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: float | None = None
    bedrooms: int | None = None
    existing_dwellings: int | None = None
    description: str | None = None
    land_area_m2: float | None = None
    existing_rent_override: float | None = None
    unit_rent_override: float | None = None
    url: str | None = None


class FeasoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interest_rate: float | None = None
    cap_months: float | None = None
    draw_factor: float | None = None
    target_yield: float | None = None
    yield_basis: str | None = None
    opex_ratio: float | None = None
    ltv_cap: float | None = None
    units: int | None = None
    unit_cost_installed: float | None = None
    misc: float | None = None
    price: float | None = None
    existing_rent_pw: float | None = None
    unit_rent_pw: float | None = None


# -------------------------------------------------------------------- jobs --
def _start_job(listings: list, rejected: list) -> dict:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "total": len(listings), "done": 0, "status": "running", "rejected": rejected,
           "results": [], "started": time.time()}
    _jobs[jid] = job

    def work():
        with _run_lock:
            def prog(i, l, s):
                job["done"] = i + 1
                job["results"].append({"address": l.address, "id": s.get("id"), "overall": s.get("overall")})
            try:
                pipe().run_batch(listings, prog)
                job["status"] = "done"
            except Exception as e:   # should not happen: run_batch guards each item
                job["status"] = f"error: {e}"
    threading.Thread(target=work, daemon=True).start()
    return job


@app.post("/api/ingest/paste")
def ingest_paste(body: PasteIn):
    src = PasteSource(body.text)
    items = list(src.listings())
    if not items:
        raise HTTPException(400, "no addresses found")
    return _start_job(items, [{"input": r, "reason": w} for r, w in src.rejected])


@app.post("/api/ingest/csv")
async def ingest_csv(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(413, "CSV too large (5 MB max)")
    src = CsvSource(raw.decode("utf-8-sig", errors="replace"))
    items = list(src.listings())
    if not items:
        raise HTTPException(400, "no rows with an address column found")
    return _start_job(items, [{"input": r, "reason": w} for r, w in src.rejected])


@app.get("/api/jobs/{jid}")
def job(jid: str):
    if jid not in _jobs:
        raise HTTPException(404, "unknown job")
    return _jobs[jid]


# ------------------------------------------------------------------- sites --
@app.get("/api/sites")
def sites():
    p = pipe()
    return ranked([s for s in (p.store.site(i) for i in p.store.all_ids()) if s])


@app.get("/api/sites/{sid}")
def site(sid: int):
    s = pipe().store.site(sid)
    if not s:
        raise HTTPException(404, "not found")
    return s


@app.patch("/api/sites/{sid}")
def patch_site(sid: int, body: ListingPatch):
    p = pipe()
    fields = body.model_dump(exclude_unset=True)
    if not p.store.update_listing_fields(sid, **fields):
        raise HTTPException(404, "not found")
    return p.recompute(sid)


@app.post("/api/sites/{sid}/refresh")
def refresh(sid: int):
    p = pipe()
    l = p.store.listing(sid)
    if not l:
        raise HTTPException(404, "not found")
    with _run_lock:
        return p.process(l, refresh_facts=True)


@app.delete("/api/sites/{sid}")
def delete(sid: int):
    pipe().store.delete(sid)
    return {"ok": True}


@app.post("/api/sites/{sid}/feaso")
def live_feaso(sid: int, body: FeasoIn):
    """Recalculate the feaso with edited inputs. Does not save."""
    p = pipe()
    s = p.store.site(sid)
    if not s:
        raise HTTPException(404, "not found")
    o = body.model_dump(exclude_none=True)
    fe = s["feaso"] or {}
    price = o.pop("price", None) or s["listing"].get("price")
    ex = o.pop("existing_rent_pw", None) or (fe.get("existing_rent") or {}).get("weekly")
    un = o.pop("unit_rent_pw", None) or (fe.get("unit_rent") or {}).get("weekly")
    missing = [n for n, v in (("price", price), ("existing rent", ex), ("unit rent", un)) if not v]
    if missing:
        return {"status": "incomplete", "note": "enter " + ", ".join(missing), "report": None}
    try:
        inp = feaso_inputs(p.cfg, price, ex, un, {k: v for k, v in o.items() if k in FEASO_KEYS})
        return {"status": "ok", "report": fz.run_with_stress(inp, stress_spec(p.cfg)).to_dict()}
    except fz.FeasoInputError as e:
        return {"status": "incomplete", "note": str(e), "report": None}


@app.get("/api/config")
def config():
    p = pipe()
    return {"feaso": p.cfg["feaso"], "gates": {k: v for k, v in p.cfg["gates"].items()},
            "assumptions": assumptions(p.cfg), "linz_enabled": p.linz.enabled}


@app.get("/api/export.csv")
def export():
    return PlainTextResponse(to_csv(sites()), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=granny-site-shortlist.csv"})


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
