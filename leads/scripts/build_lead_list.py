"""Build the lead-list page's data file (leads.json) from a gated export.

    uv run python scripts/build_lead_list.py [export_dir] [out_dir]

Defaults: the newest export under data/exports/, written to data/lead_list/ next to a copy of
web/lead_list/index.html. Only gated records are read; evidence quotes are trimmed for size.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]


def trim(text: str | None, n: int = 240) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def newest_export() -> Path:
    exports = sorted((ROOT / "data" / "exports").glob("*/manifest.json"))
    if not exports:
        raise SystemExit("no exports yet: run `leads export run`")
    return exports[-1].parent


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_export()
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "lead_list"
    out.mkdir(parents=True, exist_ok=True)
    rows = pq.read_table(src / "leads.parquet").to_pylist()
    recs = sorted(
        (
            {
                "id": r["business_id"],
                "n": r["name"],
                "c": r["category"],
                "ci": r["city"],
                "st": r["state"],
                "tz": r["tz"],
                "ph": r["phone_e164"],
                "em": r["email"],
                "w": r["website"],
                "s": round(r["score"], 1),
                "t": r["tier"],
                "o": r["opener"],
                "win": r["best_call_window"],
                "r": [
                    {"g": x["signal"], "e": trim(x["evidence"]), "u": x["source_url"]}
                    for x in r["why_reasons"]
                ],
            }
            for r in rows
            if r["compliance_status"] == "pass"
        ),
        key=lambda x: -x["s"],
    )
    m = json.loads((src / "manifest.json").read_text())
    head = {"export_id": m["export_id"], "attribution": m["attribution"], "counts": m["counts"],
            "generated": m["created_at"]}  # fmt: skip
    with (out / "leads.json").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(head)[:-1] + ',"leads":[\n')
        fh.write(",\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in recs))
        fh.write("\n]}\n")
    shutil.copy(ROOT / "web" / "lead_list" / "index.html", out / "index.html")
    print(f"{len(recs)} leads from {src.name} -> {out}")


if __name__ == "__main__":
    main()
