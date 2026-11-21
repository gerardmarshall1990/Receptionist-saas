"""Build the lead-list page's data files (index.json + leads-NNN.json parts) from a gated export.

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
PART_SIZE = 5_000


def clean(text: str | None) -> str | None:
    """Drop U+FFFD left by pages with broken encodings (the page publisher rejects it)."""
    return text.replace("\ufffd", "") if text else text


def trim(text: str | None, n: int = 240) -> str:
    t = " ".join((clean(text) or "").split())
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
                "ci": clean(r["city"]),
                "st": r["state"],
                "tz": r["tz"],
                "ph": r["phone_e164"],
                "em": r["email"],
                "w": r["website"],
                "s": round(r["score"], 1),
                "t": r["tier"],
                "o": clean(r["opener"]),
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
    # Parts of PART_SIZE leads: one published file must stay under 16 MB.
    for old in out.glob("leads*.json"):
        old.unlink()
    parts = []
    for i in range(0, len(recs), PART_SIZE):
        name = f"leads-{i // PART_SIZE:03d}.json"
        with (out / name).open("w", encoding="utf-8") as fh:
            fh.write(
                "[\n"
                + ",\n".join(
                    json.dumps(x, ensure_ascii=False, separators=(",", ":"))
                    for x in recs[i : i + PART_SIZE]
                )
                + "\n]\n"
            )
        parts.append(name)
    index = {"export_id": m["export_id"], "attribution": m["attribution"], "counts": m["counts"],
             "generated": m["created_at"], "total": len(recs), "parts": parts}  # fmt: skip
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    shutil.copy(ROOT / "web" / "lead_list" / "index.html", out / "index.html")
    print(f"{len(recs)} leads in {len(parts)} parts from {src.name} -> {out}")


if __name__ == "__main__":
    main()
