"""Read Overture Maps Places straight from its public bucket.

Nothing is bulk-downloaded: DuckDB reads only the columns a query needs, and
only from the files whose bounding box overlaps the target country (Overture
partitions files spatially). Outputs are written atomically (tmp file + rename)
and skipped if they already exist, so every step is idempotent and resumable.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb

BUCKET_URL = "https://overturemaps-us-west-2.s3.amazonaws.com"
PLACES_PREFIX = "release/{release}/theme=places/type=place/"
LICENSE_NOTE = "Overture Maps Foundation; per-record source licenses stored with each row"

# (xmin, xmax, ymin, ymax) boxes that together cover the country, used only to
# skip files. The exact filter is addresses[1].country.
COUNTRY_BOXES: dict[str, list[tuple[float, float, float, float]]] = {
    # Lower 48 + Alaska + Hawaii, plus the Aleutians west of the antimeridian.
    "US": [(-180.0, -60.0, 15.0, 72.0), (172.0, 180.0, 50.0, 54.0)],
}


@dataclass(frozen=True)
class PlaceFile:
    key: str
    size_bytes: int
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    rows: int

    @property
    def url(self) -> str:
        return f"{BUCKET_URL}/{self.key}"

    def overlaps(self, box: tuple[float, float, float, float]) -> bool:
        xmin, xmax, ymin, ymax = box
        return not (self.xmax < xmin or self.xmin > xmax or self.ymax < ymin or self.ymin > ymax)


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("load httpfs")
    con.execute("set enable_http_metadata_cache = true")
    return con


def list_release_objects(release: str) -> list[tuple[str, int]]:
    """(key, size) for every Parquet file of the release's places theme."""
    prefix = PLACES_PREFIX.format(release=release)
    out: list[tuple[str, int]] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        url = f"{BUCKET_URL}/?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https host)
            body = resp.read().decode()
        keys = re.findall(r"<Key>([^<]+)</Key>", body)
        sizes = [int(s) for s in re.findall(r"<Size>(\d+)</Size>", body)]
        out.extend((k, s) for k, s in zip(keys, sizes, strict=True) if k.endswith(".parquet"))
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", body)
        if not m:
            break
        token = m.group(1)
    if not out:
        raise RuntimeError(f"no place files found for release {release!r}")
    return out


_BBOX_STATS_SQL = """
select
  min(case when path_in_schema = 'bbox, xmin' then stats_min_value::double end),
  max(case when path_in_schema = 'bbox, xmax' then stats_max_value::double end),
  min(case when path_in_schema = 'bbox, ymin' then stats_min_value::double end),
  max(case when path_in_schema = 'bbox, ymax' then stats_max_value::double end)
from parquet_metadata(?)
"""


def build_manifest(
    con: duckdb.DuckDBPyConnection, release: str, cache_dir: Path
) -> list[PlaceFile]:
    """File list + per-file bbox (from Parquet footers only). Cached as JSON."""
    path = cache_dir / "manifest.json"
    if path.exists():
        return [PlaceFile(**d) for d in json.loads(path.read_text())]
    files: list[PlaceFile] = []
    for key, size in list_release_objects(release):
        url = f"{BUCKET_URL}/{key}"
        xmin, xmax, ymin, ymax = con.execute(_BBOX_STATS_SQL, [url]).fetchone() or (0, 0, 0, 0)
        rows_row = con.execute(
            "select sum(num_rows) from parquet_file_metadata(?)", [url]
        ).fetchone()
        rows = int(rows_row[0]) if rows_row and rows_row[0] is not None else 0
        files.append(PlaceFile(key, size, xmin, xmax, ymin, ymax, rows))
    cache_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps([asdict(f) for f in files], indent=1))
    return files


def files_for_country(files: list[PlaceFile], country: str) -> list[PlaceFile]:
    boxes = COUNTRY_BOXES[country]
    return [f for f in files if any(f.overlaps(b) for b in boxes)]


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _copy_to_parquet(
    con: duckdb.DuckDBPyConnection, select_sql: str, params: list[Any], out: Path
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    tmp.unlink(missing_ok=True)
    # The inner query takes bound parameters; only our own output path is inlined.
    con.execute(f"copy ({select_sql}) to '{tmp}' (format parquet, compression zstd)", params)
    tmp.replace(out)


def taxonomy_census(
    con: duckdb.DuckDBPyConnection, sources: list[str], country: str, out_dir: Path
) -> tuple[Path, Path]:
    """Count every taxonomy code in the country, by primary code and by alternates."""
    primary, alternates = out_dir / "census_primary.parquet", out_dir / "census_alternates.parquet"
    if not primary.exists():
        _copy_to_parquet(
            con,
            "select taxonomy.primary as code,"
            " array_to_string(taxonomy.hierarchy, '>') as hierarchy,"
            " basic_category, count(*) as n from read_parquet(?)"
            " where addresses[1].country = ? group by all",
            [sources, country],
            primary,
        )
    if not alternates.exists():
        _copy_to_parquet(
            con,
            "select alt as code, count(*) as n from ("
            " select unnest(taxonomy.alternates) as alt from read_parquet(?)"
            " where addresses[1].country = ?) group by all",
            [sources, country],
            alternates,
        )
    return primary, alternates


EXTRACT_SQL = """
select
  id                                   as source_id,
  names.primary                        as name,
  taxonomy.primary                     as code,
  taxonomy.alternates                  as alt_codes,
  confidence,
  operating_status,
  websites,
  phones,
  emails,
  socials,
  addresses[1].freeform                as address,
  addresses[1].locality                as city,
  addresses[1].postcode                as postcode,
  addresses[1].region                  as region,
  addresses[1].country                 as country,
  (bbox.ymin + bbox.ymax) / 2          as lat,
  (bbox.xmin + bbox.xmax) / 2          as lng,
  brand.wikidata                       as brand_wikidata,
  brand.names.primary                  as brand_name,
  list_transform(sources, lambda s: {'property': s.property, 'dataset': s.dataset,
    'license': s.license, 'record_id': s.record_id, 'update_time': s.update_time}) as sources
from read_parquet(?)
where taxonomy.primary in (select unnest(?::varchar[])) and addresses[1].country = ?
"""


def extract(
    con: duckdb.DuckDBPyConnection,
    sources: list[str],
    codes: list[str],
    country: str,
    out: Path,
) -> Path:
    """All places in `country` whose primary code is one of `codes`."""
    if not out.exists():
        _copy_to_parquet(con, EXTRACT_SQL, [sources, sorted(codes), country], out)
    return out
