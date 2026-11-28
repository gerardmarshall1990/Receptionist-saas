"""Rents from Tenancy Services rental bond data (quarterly, by SA2 2018, dwelling type x bedrooms).

Verified 2026-11-28: detailed-quarterly-tenancy CSV, columns
  TimeFrame, Location Id, Dwelling Type, Number Of Beds, Total Bonds, Active Bonds, Closed Bonds,
  Median Rent, Geometric Mean Rent, Upper Quartile Rent, Lower Quartile Rent, Log Std Dev Weekly Rent
TimeFrame is the first day of the quarter (d/mm/yyyy). Thin cells are published as NULL.

The Market Rent API (api.business.govt.nz) needs a subscription key and its response schema could
not be verified, so it is not implemented.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date

from .http import HttpClient, LookupFailed

STAT_COL = {"lower_quartile": "lq", "median": "med", "upper_quartile": "uq"}


@dataclass
class RentResult:
    weekly: float | None
    stat: str
    sa2_code: str
    sa2_name: str | None
    bedrooms: str
    dwelling_type: str | None
    period: str | None
    bonds: int | None
    lq: float | None = None
    med: float | None = None
    uq: float | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.weekly is not None

    def label(self) -> str:
        if not self.ok:
            return f"unavailable: {self.note}"
        where = self.sa2_name if (self.sa2_name or "").endswith(" m") else f"SA2 {self.sa2_code} {self.sa2_name or ''}".strip()
        return (f"{self.stat.replace('_', ' ')} ${self.weekly:,.0f}/wk, {self.bedrooms}-bed {self.dwelling_type}, "
                f"{where}, {self.period}, n={self.bonds} bonds")


def _quarter_label(tf: str) -> str:
    d, m, y = (int(x) for x in tf.split("/"))
    q = (m - 1) // 3 + 1
    months = ["Jan-Mar", "Apr-Jun", "Jul-Sep", "Oct-Dec"][q - 1]
    return f"Q{q} {y} ({months})"


def _tf_key(tf: str) -> int:
    d, m, y = (int(x) for x in tf.split("/"))
    return y * 100 + m


def _num(v: str) -> float | None:
    v = (v or "").strip().replace(",", "")
    if not v or v.upper() == "NULL":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def beds_key(beds: int | None) -> str | None:
    if beds is None:
        return None
    return "5+" if beds >= 5 else str(int(beds))


class BondRents:
    def __init__(self, http: HttpClient, cfg: dict, db_path: str):
        self.http = http
        self.cfg = cfg["rents"]
        self.db_path = db_path
        self.data_dir = os.path.dirname(db_path)
        with self._db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS bond_rents (
                tf INTEGER, tf_label TEXT, sa2 TEXT, dwelling TEXT, beds TEXT,
                bonds INTEGER, lq REAL, med REAL, uq REAL,
                PRIMARY KEY (tf, sa2, dwelling, beds))""")
            c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")

    def _db(self):
        return sqlite3.connect(self.db_path, timeout=30)

    def _meta(self, k: str) -> str | None:
        with self._db() as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def _discover_url(self) -> str:
        """Find the newest quarterly CSV on the bond data page; fall back to the configured URL."""
        try:
            r = self.http.session.get(self.cfg["bond_page_url"], timeout=30)
            m = re.findall(r'href="([^"]*detailed-quarterly-tenancy-q1-20[^"]*\.csv)[^"]*"', r.text)
            if m:
                u = m[0]
                return u if u.startswith("http") else "https://www.tenancy.govt.nz" + u
        except Exception:
            pass
        return self.cfg["bond_csv_url"]

    def ensure_loaded(self, force: bool = False) -> str:
        loaded = self._meta("bond_loaded_at")
        if not force and loaded and time.time() - float(loaded) < self.cfg.get("cache_days", 30) * 86400:
            return self._meta("bond_source") or ""
        url = self._discover_url()
        dest = os.path.join(self.data_dir, "bond_quarterly.csv")
        try:
            self.http.download(url, dest)
        except LookupFailed:
            if loaded:      # keep stale data rather than fail
                return self._meta("bond_source") or ""
            raise
        self.load_csv(dest, url)
        return url

    def load_csv(self, path: str, source: str) -> int:
        rows = []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                tf, loc = r.get("TimeFrame", ""), (r.get("Location Id") or "").strip()
                if not tf or not loc or loc in ("NULL", "-99"):
                    continue
                rows.append((_tf_key(tf), _quarter_label(tf), loc, r["Dwelling Type"], r["Number Of Beds"],
                             int(_num(r["Total Bonds"]) or 0), _num(r["Lower Quartile Rent"]),
                             _num(r["Median Rent"]), _num(r["Upper Quartile Rent"])))
        with self._db() as c:
            c.execute("DELETE FROM bond_rents")
            c.executemany("INSERT OR REPLACE INTO bond_rents VALUES (?,?,?,?,?,?,?,?,?)", rows)
            c.execute("INSERT OR REPLACE INTO meta VALUES ('bond_loaded_at', ?)", (str(time.time()),))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('bond_source', ?)", (source,))
        return len(rows)

    def lookup(self, sa2_code: str, sa2_name: str | None, beds: int | None, dwelling_types: list[str],
               stat: str, neighbours: list[str] | None = None) -> RentResult:
        bk = beds_key(beds)
        base = dict(stat=stat, sa2_code=sa2_code, sa2_name=sa2_name, bedrooms=bk or "?", dwelling_type=None,
                    period=None, bonds=None)
        if bk is None:
            return RentResult(None, note="bedroom count unknown", **base)
        col = STAT_COL[stat]
        with self._db() as c:
            latest = c.execute("SELECT MAX(tf), tf_label FROM bond_rents").fetchone()
            if not latest or latest[0] is None:
                return RentResult(None, note="bond data not loaded", **base)
            tf = latest[0]
            label = c.execute("SELECT tf_label FROM bond_rents WHERE tf=? LIMIT 1", (tf,)).fetchone()[0]
            n = int(self.cfg.get("pool_quarters", 4))
            tfs = [x[0] for x in c.execute("SELECT DISTINCT tf FROM bond_rents ORDER BY tf DESC LIMIT ?", (n,))]
            first = c.execute("SELECT tf_label FROM bond_rents WHERE tf=? LIMIT 1", (min(tfs),)).fetchone()[0]
            areas = [([sa2_code], f"SA2 {sa2_code} {sa2_name or ''}".strip())]
            if neighbours:
                codes = [sa2_code] + [x for x in neighbours if x != sa2_code]
                areas.append((codes, f"{len(codes)} SA2s within {self.cfg.get('neighbour_radius_m', 1500)} m"))
            # Like-for-like dwelling type matters more than recency or locality, so it is the outer loop:
            # per type -> (1) this SA2 latest quarter, (2) this SA2 last N quarters, (3) nearby SA2s last N quarters.
            for dt in dwelling_types:
                r = c.execute(f"SELECT bonds, lq, med, uq, {col} FROM bond_rents WHERE tf=? AND sa2=? AND dwelling=? AND beds=?",
                              (tf, sa2_code, dt, bk)).fetchone()
                if r and r[4] is not None and r[0] >= self.cfg["min_bonds"]:
                    return RentResult(r[4], stat, sa2_code, sa2_name, bk, dt, label, r[0], r[1], r[2], r[3])
                for codes, area in areas:
                    q = c.execute(f"SELECT bonds, lq, med, uq FROM bond_rents WHERE tf IN ({','.join('?' * len(tfs))})"
                                  f" AND sa2 IN ({','.join('?' * len(codes))}) AND dwelling=? AND beds=? AND {col} IS NOT NULL",
                                  (*tfs, *codes, dt, bk)).fetchall()
                    total = sum(x[0] for x in q)
                    if q and total >= self.cfg["min_bonds"]:
                        def w(i):
                            num = sum(x[0] * x[i] for x in q if x[i] is not None)
                            return round(num / max(1, sum(x[0] for x in q if x[i] is not None)))
                        vals = {"lq": w(1), "med": w(2), "uq": w(3)}
                        period = f"{first} to {label}, pooled bond-weighted over {len(q)} cells"
                        r = RentResult(vals[col], stat, sa2_code, sa2_name, bk, dt, period, total,
                                       vals["lq"], vals["med"], vals["uq"], note="pooled")
                        r.sa2_name = area if len(codes) > 1 else sa2_name
                        return r
        return RentResult(None, note=f"fewer than {self.cfg['min_bonds']} bonds for {bk}-bed "
                          f"({'/'.join(dwelling_types)}) near SA2 {sa2_code} {sa2_name or ''} even over {n} quarters; "
                          "enter a manual rent", **{**base, "period": label})


def today() -> str:
    return date.today().isoformat()
