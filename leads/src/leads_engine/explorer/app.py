"""FastAPI app for the lead explorer.

Read-only except one action: suppress (permanent opt-out). No export: spec section 7
says nothing leaves the system before the Stage 5 compliance gate exists.
"""

import json
import secrets
import time as _time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from leads_engine.config_loader import AppConfig
from leads_engine.explorer import facts as fx
from leads_engine.explorer import queries as qx
from leads_engine.log import get_logger
from leads_engine.settings import Settings
from leads_engine.universe.normalize import US_STATES

STATIC = Path(__file__).parent / "static"
COOKIE = "leads_token"
STATEMENT_TIMEOUT_MS = 8000
AGG_CACHE_SECONDS = 60

log = get_logger("explorer")

Tri = Literal["yes", "no", ""]


def _tri(v: Tri | None) -> bool | None:
    return True if v == "yes" else False if v == "no" else None


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, UUID):
        return str(v)
    return v


class SuppressIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    include_domain: bool = False  # also suppress every business on the same website


def create_app(
    settings: Settings,
    cfg: AppConfig,
    pool: ConnectionPool | None = None,
    agg_cache_seconds: float = AGG_CACHE_SECONDS,
) -> FastAPI:
    pool = pool or ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=12,
        kwargs={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}", "row_factory": dict_row},
        open=True,
    )
    app = FastAPI(title="Frontdesk lead explorer", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool
    token = settings.ui_token

    @contextmanager
    def conn() -> Iterator[psycopg.Connection[Any]]:
        with pool.connection() as c:
            yield c

    # -------------------------------------------------------------- auth

    @app.middleware("http")
    async def auth(request: Request, call_next: Callable[[Request], Any]) -> Response:
        # CSRF: browsers can only send cross-site POSTs as form/text bodies without a
        # preflight, so every state-changing API call must be application/json.
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get(
            "content-type", ""
        ).startswith("application/json"):
            return JSONResponse({"detail": "application/json required"}, status_code=415)
        if token is None or request.url.path == "/api/health":
            resp: Response = await call_next(request)
        else:
            supplied = request.query_params.get("token")
            if supplied is not None and request.url.path == "/":
                if not secrets.compare_digest(supplied, token):
                    return HTMLResponse("Wrong token.", status_code=401)
                r = RedirectResponse("/", status_code=303)
                r.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=30 * 86400)
                return r
            given = request.cookies.get(COOKIE) or request.headers.get("x-leads-token") or ""
            if not secrets.compare_digest(given, token):
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"detail": "unauthorized"}, status_code=401)
                return HTMLResponse(
                    "Open this page with <code>/?token=YOUR_TOKEN</code> once.", status_code=401
                )
            resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self';"
            " connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return resp

    # ----------------------------------------------------------- filters

    def filters(
        category: Annotated[list[str], Query()] = [],  # noqa: B006 (FastAPI query lists)
        subcategory: Annotated[list[str], Query()] = [],  # noqa: B006
        state: Annotated[list[str], Query()] = [],  # noqa: B006
        tier: Annotated[list[str], Query()] = [],  # noqa: B006
        flag: Annotated[list[str], Query()] = [],  # noqa: B006
        not_flag: Annotated[list[str], Query()] = [],  # noqa: B006
        q: Annotated[str | None, Query(max_length=100)] = None,
        has_phone: Tri | None = None,
        has_website: Tri | None = None,
        has_email: Tri | None = None,
        callable_now: bool = False,
        include_suppressed: bool = False,
    ) -> qx.Filters:
        subs = {s.slug for c in cfg.categories.values() for s in c.subcategories}
        checks = (
            ("category", category, set(cfg.categories)),
            ("subcategory", subcategory, subs),
            ("state", state, US_STATES),
            ("tier", tier, {"A", "B", "C", "D"}),
            ("flag", flag, set(qx.FLAG_LABELS)),
            ("not_flag", not_flag, set(qx.FLAG_LABELS)),
        )
        for name, values, allowed in checks:
            bad = sorted(set(values) - allowed)
            if bad:
                raise HTTPException(422, f"unknown {name}: {bad}")
        pairs: tuple[tuple[str, str], ...] = ()
        if callable_now:
            pairs = tuple(
                (cat, tz) for cat, tz in tz_pairs() if fx.callable_now(cfg.categories.get(cat), tz)
            )
        return qx.Filters(
            callable_pairs=pairs,
            suppressed=suppressed_list(),
            category=tuple(category), subcategory=tuple(subcategory), state=tuple(state),
            tier=tuple(tier), q=q, has_phone=_tri(has_phone), has_website=_tri(has_website),
            has_email=_tri(has_email), flags_any=tuple(flag), flags_none=tuple(not_flag),
            callable_now=callable_now, include_suppressed=include_suppressed,
        )  # fmt: skip

    F = Annotated[qx.Filters, Depends(filters)]

    tz_cache: dict[str, Any] = {"at": 0.0, "pairs": []}

    def tz_pairs() -> list[tuple[str, str]]:
        """Distinct (category, tz) in the table; refreshed every 10 minutes."""
        if _time.monotonic() - tz_cache["at"] > 600:
            with conn() as c:
                rows = c.execute(
                    "select distinct category, tz from businesses where tz is not null"
                ).fetchall()
            tz_cache.update(at=_time.monotonic(), pairs=[(r["category"], r["tz"]) for r in rows])
        pairs: list[tuple[str, str]] = tz_cache["pairs"]
        return pairs

    def suppressed_list() -> qx.Suppressed:
        with conn() as c:
            rows = c.execute(
                "select match_type, match_value from suppression"
                " where match_type in ('business_id', 'phone_e164', 'domain')"
            ).fetchall()
        by: dict[str, list[str]] = {"business_id": [], "phone_e164": [], "domain": []}
        for r in rows:
            by[r["match_type"]].append(r["match_value"])
        return qx.Suppressed(tuple(by["business_id"]), tuple(by["phone_e164"]), tuple(by["domain"]))

    # -------------------------------------------------------------- pages

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # --------------------------------------------------------------- meta

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        with conn() as c:
            row = c.execute(
                "select count(*) as n, count(*) filter (where in_pilot_sample) as pilot,"
                " max(source_release) as release from businesses"
            ).fetchone()
        if row is None:
            raise HTTPException(500, "database returned no row")
        return {
            "total": row["n"],
            "pilot_sample": row["pilot"],
            "release": row["release"],
            "config_hash": cfg.config_hash,
            "categories": [
                {
                    "slug": c.slug,
                    "label": c.label,
                    "subcategories": [
                        {"slug": s.slug, "label": s.label} for s in c.subcategories if s.include
                    ],
                    "call_windows": [
                        {
                            "days": [d.value for d in w.days],
                            "start": w.start.strftime("%H:%M"),
                            "end": w.end.strftime("%H:%M"),
                            "why": w.why,
                        }
                        for w in c.best_call_windows
                    ],
                }
                for c in sorted(cfg.categories.values(), key=lambda c: c.rank)
            ],
            "states": sorted(US_STATES),
            "flags": [{"key": k, "label": v} for k, v in qx.FLAG_LABELS.items()],
            "sorts": [{"key": k, "label": s.label} for k, s in qx.SORTS.items()],
            "export": {
                "enabled": False,
                "why": "Export opens after the Stage 5 compliance gate (spec section 7).",
            },
        }

    # -------------------------------------------------------------- leads

    @app.get("/api/leads")
    def leads(
        f: F,
        sort: str = qx.DEFAULT_SORT,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        if sort not in qx.SORTS:
            raise HTTPException(422, f"unknown sort: {sort}")
        try:
            query, params = qx.list_query(f, cfg, sort, cursor, limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        t0 = _time.monotonic()
        with conn() as c:
            rows = c.execute(query, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        nkeys = len(qx.SORTS[sort].keys)
        items = []
        for r in rows:
            lt = fx.local_time(r["tz"])
            cat = cfg.categories.get(r["category"])
            items.append(
                {k: _jsonable(v) for k, v in r.items() if not k.startswith("_k")}
                | {
                    "local_time": lt.strftime("%a %H:%M") if lt else None,
                    "callable_now": fx.callable_now(cat, r["tz"]),
                }
            )
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = qx.encode_cursor([_jsonable(last[f"_k{i}"]) for i in range(nkeys)])
        return {
            "items": items,
            "next_cursor": next_cursor,
            "ms": round((_time.monotonic() - t0) * 1000),
        }

    # Counts and facets are aggregate scans; cache them briefly (data only changes on
    # load or suppress, and suppress clears the cache).
    agg_cache: dict[str, tuple[float, Any]] = {}

    def cached(key: str, compute: Callable[[], Any]) -> Any:
        hit = agg_cache.get(key)
        if hit and _time.monotonic() - hit[0] < agg_cache_seconds:
            return hit[1]
        value = compute()
        if len(agg_cache) > 2000:
            agg_cache.clear()
        agg_cache[key] = (_time.monotonic(), value)
        return value

    @app.get("/api/count")
    def count(f: F, request: Request) -> dict[str, Any]:
        key = "count?" + str(request.query_params)
        if f.callable_now:  # depends on the clock, never cache
            return compute_count(f)
        result: dict[str, Any] = cached(key, lambda: compute_count(f))
        return result

    def compute_count(f: qx.Filters) -> dict[str, Any]:
        query, params = qx.count_query(f, cfg)
        with conn() as c:
            row = c.execute(query, params).fetchone()
            n = int(row["count"]) if row else 0
            if n <= qx.COUNT_CAP:
                return {"value": n, "exact": True}
            eq, ep = qx.estimate_query(f, cfg)
            plan = c.execute(eq, ep).fetchone()
        est = int(json.loads(json.dumps(plan["QUERY PLAN"]))[0]["Plan"]["Plan Rows"]) if plan else n
        return {"value": max(est, qx.COUNT_CAP + 1), "exact": False}

    @app.get("/api/facets")
    def facets(
        f: F,
        request: Request,
        facet: Annotated[list[str], Query()] = ["category", "state"],  # noqa: B006
    ) -> dict[str, Any]:
        for name in facet:
            if name not in qx.FACET_COLUMNS:
                raise HTTPException(422, f"unknown facet: {name}")
        if f.callable_now:
            return compute_facets(f, facet)
        result: dict[str, Any] = cached(
            "facets?" + str(request.query_params), lambda: compute_facets(f, facet)
        )
        return result

    def compute_facets(f: qx.Filters, facet: list[str]) -> dict[str, Any]:
        # Independent aggregate scans: run them concurrently on separate pooled
        # connections, so wall time is the slowest facet, not the sum. (A single
        # GROUPING SETS scan was measured and is slower: it loses parallel hash agg.)
        def one(name: str) -> tuple[str, list[dict[str, Any]]]:
            query, params = qx.facet_query(f, cfg, name)
            with conn() as c:
                return name, [{"value": r["v"], "count": r["n"]} for r in c.execute(query, params)]

        names = list(dict.fromkeys(facet))
        with ThreadPoolExecutor(max_workers=len(names)) as ex:
            return dict(ex.map(one, names))

    @app.get("/api/leads/{lead_id}")
    def lead(lead_id: UUID) -> dict[str, Any]:
        with conn() as c:
            b = c.execute(qx.DETAIL_SQL, (lead_id,)).fetchone()
            if b is None:
                raise HTTPException(404, "lead not found")
            phones = c.execute(
                "select e164, sources, agree_count, valid_format, number_type_guess, line_type,"
                " carrier, flags, status, verified_at from phones"
                " where business_id = %s order by e164",
                (lead_id,),
            ).fetchall()
            emails = c.execute(
                "select email, origin, source_url, syntax_ok, is_role_based, is_freemail,"
                " domain_matches_site, flags, mx_ok, deliverability from emails"
                " where business_id = %s order by origin, email",
                (lead_id,),
            ).fetchall()
            reasons = c.execute(
                "select signal, dimension, evidence, source_url, weight, stage from reasons"
                " where business_id = %s order by weight desc, id",
                (lead_id,),
            ).fetchall()
            supp = c.execute(
                "select match_type, match_value, reason, source, created_at from suppression"
                " where (match_type = 'business_id' and match_value = %s)"
                " or (match_type = 'phone_e164' and match_value = %s)"
                " or (match_type = 'domain' and match_value = %s) order by created_at",
                (str(lead_id), b["phone"], b["domain"]),
            ).fetchall()
        cat = cfg.categories.get(b["category"])
        lt = fx.local_time(b["tz"])
        business = {k: _jsonable(v) for k, v in b.items() if k not in {"search_text"}}
        return {
            "business": business,
            "phones": [{k: _jsonable(v) for k, v in p.items()} for p in phones],
            "emails": emails,
            "reasons": [{k: _jsonable(v) for k, v in r.items()} for r in reasons],
            "facts": fx.facts_for(b, phones, emails),
            "suppression": supp,
            "local_time": lt.strftime("%a %H:%M") if lt else None,
            "callable_now": fx.callable_now(cat, b["tz"]),
            "category_label": cat.label if cat else b["category"],
        }

    @app.post("/api/leads/{lead_id}/suppress")
    def suppress(lead_id: UUID, body: SuppressIn) -> dict[str, Any]:
        # JSON-only is enforced in the middleware; the cookie is SameSite=Strict too.
        with conn() as c, c.transaction():
            b = c.execute(
                "select id, phone, domain from businesses where id = %s", (lead_id,)
            ).fetchone()
            if b is None:
                raise HTTPException(404, "lead not found")
            phones = [
                r["e164"]
                for r in c.execute("select e164 from phones where business_id = %s", (lead_id,))
            ]
            emails = [
                r["email"]
                for r in c.execute("select email from emails where business_id = %s", (lead_id,))
            ]
            matches = [("business_id", str(lead_id))] + [("phone_e164", p) for p in phones]
            matches += [("email", e) for e in emails]
            if body.include_domain and b["domain"]:
                matches.append(("domain", b["domain"]))
            inserted = 0
            for mt, mv in matches:
                cur = c.execute(
                    "insert into suppression (match_type, match_value, reason, source)"
                    " values (%s, %s, %s, 'explorer-ui') on conflict do nothing",
                    (mt, mv, body.reason),
                )
                inserted += cur.rowcount
        agg_cache.clear()
        log.info(
            "suppressed",
            business_id=str(lead_id),
            rows=inserted,
            include_domain=body.include_domain,
        )
        return {"suppressed": True, "rows_added": inserted, "matches": len(matches)}

    return app
