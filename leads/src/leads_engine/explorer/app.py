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
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from leads_engine.compliance import export as cx
from leads_engine.config_loader import AppConfig
from leads_engine.explorer import facts as fx
from leads_engine.explorer import google as gx
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
    places_client: gx.PlacesClient | None = None,
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
            "default-src 'self'; img-src 'self' data: https://lh3.googleusercontent.com;"
            " style-src 'self'; script-src 'self';"
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
        signal: Annotated[list[str], Query()] = [],  # noqa: B006
        q: Annotated[str | None, Query(max_length=100)] = None,
        has_phone: Tri | None = None,
        has_website: Tri | None = None,
        has_email: Tri | None = None,
        callable_now: bool = False,
        include_suppressed: bool = False,
        compliance: Literal["pass", "blocked", "pending"] | None = None,
    ) -> qx.Filters:
        subs = {s.slug for c in cfg.categories.values() for s in c.subcategories}
        checks = (
            ("category", category, set(cfg.categories)),
            ("subcategory", subcategory, subs),
            ("state", state, US_STATES),
            ("tier", tier, {"A", "B", "C", "D"}),
            ("flag", flag, set(qx.FLAG_LABELS)),
            ("not_flag", not_flag, set(qx.FLAG_LABELS)),
            ("signal", signal, set(qx.SIGNAL_FILTERS)),
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
            signals=tuple(signal), callable_now=callable_now, include_suppressed=include_suppressed,
            compliance=compliance,
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
            "signals": [{"key": k, "label": v} for k, v in qx.SIGNAL_FILTERS.items()],
            "sorts": [{"key": k, "label": s.label} for k, s in qx.SORTS.items()],
            "export": {
                "enabled": True,
                "max_rows": cfg.compliance.export.ui_max_rows,
                "min_tier": cfg.compliance.export.min_tier,
                "calling_mode": cfg.compliance.calling_mode,
                "why": (
                    "Exports only leads that pass the compliance gate, re-checked at export time"
                    f" (tier {cfg.compliance.export.min_tier} or better, phone confirmed on their"
                    " own site, not suppressed). Every export is recorded."
                ),
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
            score_row = c.execute(
                "select pain, value, reachability, fit, total, tier, dead_reason, components,"
                " model_version from scores where business_id = %s",
                (lead_id,),
            ).fetchone()
            signal_rows = c.execute(
                "select signal, value, count, evidence, source_url, extractor_version from signals"
                " where business_id = %s order by value desc, signal",
                (lead_id,),
            ).fetchall()
            compliance = cx.gate_one(c, lead_id, cfg)
            # Call history (Stage 6). The experiment arm is deliberately NOT shown: callers
            # use this screen, and the pilot is blinded.
            calls = c.execute(
                "select called_at, caller, disposition, demo_booked, trial_started, paid, notes"
                " from outcomes where business_id = %s order by called_at desc",
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
            "signals": signal_rows,
            "score": {k: _jsonable(v) for k, v in score_row.items()} if score_row else None,
            "facts": fx.facts_for(b, phones, emails),
            "suppression": supp,
            "local_time": lt.strftime("%a %H:%M") if lt else None,
            "callable_now": fx.callable_now(cat, b["tz"]),
            "category_label": cat.label if cat else b["category"],
            "compliance": compliance,
            "calls": [{k: _jsonable(v) for k, v in r.items()} for r in calls],
        }

    places_holder: dict[str, gx.PlacesClient] = {}

    def places() -> gx.PlacesClient:
        if "c" not in places_holder:
            places_holder["c"] = places_client or gx.PlacesClient(settings.google_places_api_key)
        return places_holder["c"]

    @app.post("/api/leads/{lead_id}/google")
    def google_reviews(lead_id: UUID) -> dict[str, Any]:
        """Live Google rating + up to 5 reviews. Costs one Place Details call (capped)."""
        with conn() as c:
            lead = c.execute(
                "select id, name, address, city, state, postcode, lat, lng, phone, category,"
                " google_place_id,"
                " array(select e164 from phones p where p.business_id = b.id) as phones"
                " from businesses b where id = %s",
                (lead_id,),
            ).fetchone()
            if lead is None:
                raise HTTPException(404, "lead not found")
            try:
                res = gx.live_reviews(c, places(), lead, cfg.global_.google_places_monthly_cap)
            except gx.CapReachedError as exc:
                raise HTTPException(429, str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code in {401, 403}:
                    raise HTTPException(
                        503, "Google Places API key missing or not authorized"
                    ) from exc
                raise HTTPException(502, f"Google Places error {code}") from exc
            except httpx.HTTPError as exc:
                raise HTTPException(
                    502, f"Google Places unreachable: {type(exc).__name__}"
                ) from exc
        cat = cfg.categories.get(lead["category"])
        p = res.place
        return {
            "status": res.status,
            "detail": res.detail,
            "place": {
                "name": (p.get("displayName") or {}).get("text"),
                "address": p.get("formattedAddress"),
                "phone": p.get("internationalPhoneNumber"),
                "rating": p.get("rating"),
                "rating_count": p.get("userRatingCount"),
                "maps_url": p.get("googleMapsUri"),
                "reviews": [
                    {
                        "author": (r.get("authorAttribution") or {}).get("displayName"),
                        "author_url": (r.get("authorAttribution") or {}).get("uri"),
                        "author_photo": (r.get("authorAttribution") or {}).get("photoUri"),
                        "rating": r.get("rating"),
                        "when": r.get("relativePublishTimeDescription"),
                        "text": (r.get("text") or r.get("originalText") or {}).get("text", ""),
                        "url": r.get("googleMapsUri"),
                    }
                    for r in p.get("reviews") or []
                ],
            }
            if res.status == "matched"
            else None,
            "highlight": sorted(
                set(cat.review_pain_keywords if cat else []) | set(gx.GENERIC_PAIN)
            ),
            "notice": "Up to 5 reviews, chosen and ordered by Google by relevance."
            " Reviews aren't verified by Google, but Google checks for and removes fake"
            " content when it's identified."
            " Shown live from Google Maps; not stored.",
        }

    @app.post("/api/export")
    def export(f: F) -> Response:
        """Gate the leads matching the current filters and download the passing ones as CSV."""
        w = qx.build_where(f, cfg)
        request = {k: v for k, v in asdict(f).items() if k not in {"suppressed", "callable_pairs"}}
        res = cx.run_export(
            settings.database_url, cfg, where=w.sql(), params=w.params, origin="explorer",
            out_root=settings.data_dir / "exports", limit=cfg.compliance.export.ui_max_rows,
            request=request,
        )  # fmt: skip
        log.info("explorer_export", export_id=res.export_id, **res.counts)
        return Response(
            cx.to_csv(res.records),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="leads-{res.export_id[:8]}.csv"',
                "X-Export-Id": res.export_id,
                "X-Export-Passed": str(res.counts.get("pass", 0)),
                "X-Export-Blocked": str(res.counts.get("blocked", 0)),
                "Cache-Control": "no-store",
            },
        )

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
            c.execute(
                "update businesses set compliance_status = 'blocked',"
                " compliance_blocks = array_append(compliance_blocks, 'suppressed')"
                " where compliance_status = 'pass' and (id = %s or phone = any(%s)"
                " or (%s and domain = %s))",
                (lead_id, phones, body.include_domain, b["domain"]),
            )
        agg_cache.clear()
        log.info(
            "suppressed",
            business_id=str(lead_id),
            rows=inserted,
            include_domain=body.include_domain,
        )
        return {"suppressed": True, "rows_added": inserted, "matches": len(matches)}

    return app
