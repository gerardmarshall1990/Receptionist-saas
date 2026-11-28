"""Command line.

  python -m gsf.cli paste "12 Sandringham Rd, Kingsland | 1050000 | 3"   # or pipe lines on stdin
  python -m gsf.cli import listings.csv
  python -m gsf.cli rerun                 # re-evaluate everything stored (refetch lookups)
  python -m gsf.cli report                # write CSV + markdown shortlist to data/reports/
  python -m gsf.cli daily listings.csv    # import + report in one go (cron-friendly)
  python -m gsf.cli sheet <sheet-url>     # import new listings from the shared sheet's Inbox tab
  python -m gsf.cli xlsx                  # shortlist workbook for Google Sheets
  python -m gsf.cli serve                 # web UI on http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from .config import load_config, load_env
from .pipeline import Pipeline
from .report import ranked, to_csv, to_markdown
from .sources.csv_source import CsvSource
from .sources.paste import PasteSource


def _progress(i, listing, site):
    st = site.get("overall", "?")
    fails = [g["reason"] for g in site.get("gates", []) if g["hard"] and g["status"] == "fail"]
    print(f"  [{i + 1}] {st.upper():10} {listing.address}" + (f"  — {fails[0]}" if fails else ""), flush=True)


def _ingest(p: Pipeline, src) -> None:
    items = list(src.listings())
    for raw, why in src.rejected:
        print(f"  skipped: {raw!r}: {why}")
    print(f"Evaluating {len(items)} listing(s)…")
    p.run_batch(items, _progress)


def write_report(p: Pipeline) -> tuple[str, str]:
    rows = ranked([p.store.site(i) for i in p.store.all_ids()])
    d = p.cfg["storage"].get("reports_dir", "data/reports")
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    csv_path, md_path = os.path.join(d, f"shortlist-{stamp}.csv"), os.path.join(d, f"shortlist-{stamp}.md")
    with open(csv_path, "w", newline="") as fh:
        fh.write(to_csv(rows))
    with open(md_path, "w") as fh:
        fh.write(to_markdown(rows, p.cfg))
    return csv_path, md_path


def main(argv: list[str] | None = None) -> int:
    load_env()
    ap = argparse.ArgumentParser(prog="gsf", description="Granny-Site Finder")
    ap.add_argument("--config", help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("paste", help="evaluate pasted addresses (args or stdin)")
    s.add_argument("lines", nargs="*")
    s = sub.add_parser("import", help="import a CSV of listings")
    s.add_argument("path")
    sub.add_parser("rerun", help="re-evaluate all stored listings")
    sub.add_parser("report", help="write shortlist CSV + markdown")
    s = sub.add_parser("daily", help="import CSV (optional) then write report")
    s.add_argument("path", nargs="?")
    s = sub.add_parser("sheet", help="import listings from a Google Sheet's Inbox tab (link-shared)")
    s.add_argument("url")
    s = sub.add_parser("xlsx", help="write the shortlist workbook (for Google Sheets)")
    s.add_argument("path", nargs="?", default=None)
    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)

    if a.config:
        os.environ["GSF_CONFIG"] = a.config
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("gsf.api:app", host=a.host, port=a.port)
        return 0
    p = Pipeline(load_config(a.config))
    if a.cmd == "paste":
        text = "\n".join(a.lines) if a.lines else sys.stdin.read()
        _ingest(p, PasteSource(text))
    elif a.cmd == "import":
        _ingest(p, CsvSource(path=a.path))
    elif a.cmd == "sheet":
        from .sources.gsheet import GoogleSheetSource
        _ingest(p, GoogleSheetSource(a.url))
    elif a.cmd == "rerun":
        ids = p.store.all_ids()
        print(f"Re-evaluating {len(ids)} listing(s)…")
        p.run_batch([p.store.listing(i) for i in ids], _progress)
    elif a.cmd == "daily" and a.path:
        _ingest(p, CsvSource(path=a.path))
    if a.cmd == "xlsx":
        from .report import ranked
        from .xlsx_export import build_workbook
        sites = [p.store.site(i) for i in p.store.all_ids()]
        out = a.path or os.path.join(p.cfg["storage"].get("reports_dir", "data/reports"), "granny-site-shortlist.xlsx")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print("Workbook:", build_workbook(ranked(sites), sites, p.cfg, out))
        return 0
    if a.cmd in ("report", "daily", "paste", "import", "rerun", "sheet"):
        c, m = write_report(p)
        print(f"Report: {m}\nCSV:    {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
