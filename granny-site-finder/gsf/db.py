"""SQLite storage. Re-running a listing upserts; it never duplicates rows."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  address TEXT NOT NULL,
  data TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  UNIQUE (source, source_id)
);
CREATE TABLE IF NOT EXISTS sites (
  listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
  facts TEXT NOT NULL,
  overall TEXT NOT NULL,
  evaluated_at REAL NOT NULL,
  evaluated_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_results (
  listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  gate TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  hard INTEGER NOT NULL,
  ord INTEGER NOT NULL,
  PRIMARY KEY (listing_id, gate)
);
CREATE TABLE IF NOT EXISTS feaso_results (
  listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
  status TEXT NOT NULL,           -- ok | incomplete
  note TEXT,
  report TEXT,                    -- FeasoReport.to_dict()
  existing_rent TEXT,
  unit_rent TEXT
);
"""


def content_hash(listing: Listing) -> str:
    d = listing.to_dict()
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def upsert_listing(self, listing: Listing) -> tuple[int, bool]:
        """Returns (listing_id, changed)."""
        src, sid = listing.key()
        h = content_hash(listing)
        now = time.time()
        with self.conn() as c:
            row = c.execute("SELECT id, content_hash FROM listings WHERE source=? AND source_id=?", (src, sid)).fetchone()
            if row:
                c.execute("UPDATE listings SET address=?, data=?, content_hash=?, last_seen=? WHERE id=?",
                          (listing.address, json.dumps(listing.to_dict()), h, now, row["id"]))
                return row["id"], row["content_hash"] != h
            cur = c.execute("INSERT INTO listings (source, source_id, address, data, content_hash, first_seen, last_seen)"
                            " VALUES (?,?,?,?,?,?,?)", (src, sid, listing.address, json.dumps(listing.to_dict()), h, now, now))
            return cur.lastrowid, True

    def save_evaluation(self, listing_id: int, facts: dict, overall: str, gates: list[dict],
                        feaso: dict, evaluated_hash: str) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO sites VALUES (?,?,?,?,?)",
                      (listing_id, json.dumps(facts), overall, time.time(), evaluated_hash))
            c.execute("DELETE FROM gate_results WHERE listing_id=?", (listing_id,))
            c.executemany("INSERT INTO gate_results VALUES (?,?,?,?,?,?)",
                          [(listing_id, g["gate"], g["status"], g["reason"], int(g["hard"]), i) for i, g in enumerate(gates)])
            c.execute("INSERT OR REPLACE INTO feaso_results VALUES (?,?,?,?,?,?)",
                      (listing_id, feaso["status"], feaso.get("note"), json.dumps(feaso.get("report")),
                       json.dumps(feaso.get("existing_rent")), json.dumps(feaso.get("unit_rent"))))

    def listing(self, listing_id: int) -> Listing | None:
        with self.conn() as c:
            r = c.execute("SELECT data FROM listings WHERE id=?", (listing_id,)).fetchone()
        return Listing(**json.loads(r["data"])) if r else None

    def update_listing_fields(self, listing_id: int, **fields) -> Listing | None:
        l = self.listing(listing_id)
        if not l:
            return None
        for k, v in fields.items():
            if hasattr(l, k):
                setattr(l, k, v)
        with self.conn() as c:
            c.execute("UPDATE listings SET data=?, content_hash=? WHERE id=?",
                      (json.dumps(l.to_dict()), content_hash(l), listing_id))
        return l

    def delete(self, listing_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM listings WHERE id=?", (listing_id,))

    def site(self, listing_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            l = c.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
            if not l:
                return None
            s = c.execute("SELECT * FROM sites WHERE listing_id=?", (listing_id,)).fetchone()
            g = c.execute("SELECT gate, status, reason, hard FROM gate_results WHERE listing_id=? ORDER BY ord",
                          (listing_id,)).fetchall()
            f = c.execute("SELECT * FROM feaso_results WHERE listing_id=?", (listing_id,)).fetchone()
        return {
            "id": l["id"], "listing": json.loads(l["data"]), "source": l["source"],
            "first_seen": l["first_seen"], "last_seen": l["last_seen"],
            "facts": json.loads(s["facts"]) if s else None,
            "overall": s["overall"] if s else "pending",
            "evaluated_at": s["evaluated_at"] if s else None,
            "gates": [dict(x) | {"hard": bool(x["hard"])} for x in g],
            "feaso": None if not f else {
                "status": f["status"], "note": f["note"], "report": json.loads(f["report"] or "null"),
                "existing_rent": json.loads(f["existing_rent"] or "null"),
                "unit_rent": json.loads(f["unit_rent"] or "null"),
            },
        }

    def all_ids(self) -> list[int]:
        with self.conn() as c:
            return [r[0] for r in c.execute("SELECT id FROM listings ORDER BY id")]

    def counts(self) -> dict[str, int]:
        with self.conn() as c:
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("listings", "sites", "gate_results", "feaso_results")}
