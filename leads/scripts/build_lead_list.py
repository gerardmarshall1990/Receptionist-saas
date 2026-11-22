"""Build the lead-list page's data from a gated export.

    uv run python scripts/build_lead_list.py [export_dir] [out_dir]

Writes, under out_dir:
- <export8>/list-NNN.json   light rows for the table and filters (~150 bytes a lead)
- <export8>/d-NN.json       64 detail shards (reasons, email, website, call window, opener), keyed by
                            int(id[:2], 16) % 64 and fetched only when a lead is opened
- index.json                points at the current <export8> folder (publish it LAST)
- publish_plan.json         batches of files under 55 MB for the artifact publisher, then the index,
                            then removal of the previous export's folder

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
LIST_PART = 25_000
SHARDS = 64
BATCH_BYTES = 55_000_000


def clean(text: str | None) -> str | None:
    """Drop U+FFFD left by pages with broken encodings (the page publisher rejects it)."""
    return text.replace("�", "") if text else text


def trim(text: str | None, n: int = 220) -> str:
    t = " ".join((clean(text) or "").split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def shard_of(business_id: str) -> int:
    return int(business_id[:2], 16) % SHARDS


def newest_export() -> Path:
    exports = sorted((ROOT / "data" / "exports").glob("*/manifest.json"))
    if not exports:
        raise SystemExit("no exports yet: run `leads export run`")
    return exports[-1].parent


def dump(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_export()
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "lead_list"
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads((src / "manifest.json").read_text())
    folder = m["export_id"][:8]
    old_index = (
        json.loads((out / "index.json").read_text()) if (out / "index.json").exists() else {}
    )
    old_files = [*old_index.get("list", []), *old_index.get("shard_files", [])]
    old_files += [f for f in old_index.get("parts", [])]  # pre-shard layout
    if old_index.get("parts"):
        old_files.append("leads.json")

    rows = [
        r
        for r in pq.read_table(src / "leads.parquet").to_pylist()
        if r["compliance_status"] == "pass"
    ]
    rows.sort(key=lambda r: -r["score"])
    for d in out.iterdir():  # one export folder at a time locally
        if d.is_dir() and d.name != folder:
            shutil.rmtree(d)
    (out / folder).mkdir(exist_ok=True)

    light = [
        {
            "id": r["business_id"],
            "n": clean(r["name"]),
            "c": r["category"],
            "ci": clean(r["city"]),
            "st": r["state"],
            "ph": r["phone_e164"],
            "s": round(r["score"], 1),
            "t": r["tier"],
            "g": [x["signal"] for x in r["why_reasons"]],
            "e": bool(r["email"]),
            "o": bool(r["opener"]),
        }
        for r in rows
    ]
    shards: list[dict[str, object]] = [{} for _ in range(SHARDS)]
    for r in rows:
        shards[shard_of(r["business_id"])][r["business_id"]] = {
            "em": r["email"], "w": r["website"], "win": r["best_call_window"], "o": clean(r["opener"]),
            "r": [{"g": x["signal"], "e": trim(x["evidence"]), "u": x["source_url"]} for x in r["why_reasons"]],
        }  # fmt: skip

    list_files = []
    for i in range(0, len(light), LIST_PART):
        name = f"{folder}/list-{i // LIST_PART:03d}.json"
        dump(out / name, light[i : i + LIST_PART])
        list_files.append(name)
    shard_files = []
    for i, shard in enumerate(shards):
        name = f"{folder}/d-{i:02d}.json"
        dump(out / name, shard)
        shard_files.append(name)

    index = {"export_id": m["export_id"], "attribution": m["attribution"], "counts": m["counts"],
             "generated": m["created_at"], "total": len(rows), "list": list_files, "shard_dir": folder,
             "shards": SHARDS, "shard_files": shard_files}  # fmt: skip
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    shutil.copy(ROOT / "web" / "lead_list" / "index.html", out / "index.html")

    batches: list[dict[str, str]] = []
    size = 0
    for name in [*list_files, *shard_files]:
        n = (out / name).stat().st_size
        if not batches or size + n > BATCH_BYTES:
            batches.append({})
            size = 0
        batches[-1][name] = str(out / name)
        size += n
    final: dict[str, str | None] = {"index.json": str(out / "index.json")}
    final.update({f: None for f in old_files if not f.startswith(folder + "/")})
    plan = {"batches": batches, "final": final}
    (out / "publish_plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    total = sum((out / f).stat().st_size for f in [*list_files, *shard_files])
    print(f"{len(rows)} leads -> {out}/{folder}: {len(list_files)} list part(s), {SHARDS} shards, "
          f"{total / 1e6:.0f} MB in {len(batches)} batch(es); removes {len(final) - 1} old file(s)")  # fmt: skip


if __name__ == "__main__":
    main()
