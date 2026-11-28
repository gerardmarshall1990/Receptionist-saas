"""HTTP client with SQLite response cache, retries with backoff, per-host rate limit and timeouts.

Failures surface as LookupFailed with a short human-readable message; callers turn that into
an 'unverified' fact instead of crashing the batch.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests


class LookupFailed(RuntimeError):
    pass


class HttpClient:
    def __init__(self, db_path: str, timeout_s: float = 30, retries: int = 3, backoff_s: float = 1.5,
                 min_interval_s: float = 0.2, user_agent: str = "granny-site-finder/0.1"):
        self.db_path = db_path
        self.timeout = timeout_s
        self.retries = retries
        self.backoff = backoff_s
        self.min_interval = min_interval_s
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.stats = {"hits": 0, "misses": 0, "errors": 0}
        with self._db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS http_cache (k TEXT PRIMARY KEY, url TEXT, body TEXT, fetched REAL)")

    def _db(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    @staticmethod
    def _key(method: str, url: str, params: dict | None) -> str:
        blob = json.dumps([method, url, sorted((params or {}).items())], default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def request_json(self, method: str, url: str, params: dict[str, Any] | None = None,
                     cache_days: float | None = 7, headers: dict | None = None,
                     cache_url_label: str | None = None) -> Any:
        k = self._key(method, cache_url_label or url, params)
        if cache_days:
            with self._db() as c:
                row = c.execute("SELECT body, fetched FROM http_cache WHERE k=?", (k,)).fetchone()
            if row and time.time() - row[1] < cache_days * 86400:
                self.stats["hits"] += 1
                return json.loads(row[0])
        self.stats["misses"] += 1
        body = self._fetch(method, url, params, headers)
        if cache_days:
            with self._db() as c:
                c.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?)",
                          (k, cache_url_label or url, json.dumps(body), time.time()))
        return body

    def _fetch(self, method, url, params, headers) -> Any:
        host = urlparse(url).netloc
        last_err = "unknown error"
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                if method == "POST":
                    r = self.session.post(url, data=params, timeout=self.timeout, headers=headers)
                else:
                    r = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
                if r.status_code == 429 or r.status_code >= 500:
                    retry_after = r.headers.get("Retry-After")
                    last_err = f"{host} returned HTTP {r.status_code}"
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff * 2 ** attempt
                    time.sleep(min(delay, 30))
                    continue
                if r.status_code in (401, 403):
                    raise LookupFailed(f"{host} refused access (HTTP {r.status_code}); check API key")
                if r.status_code >= 400:
                    raise LookupFailed(f"{host} returned HTTP {r.status_code}")
                data = r.json()
                if isinstance(data, dict) and "error" in data and isinstance(data["error"], dict):
                    # ArcGIS reports errors with HTTP 200
                    code = data["error"].get("code")
                    msg = data["error"].get("message", "error")
                    if code in (429, 500, 502, 503, 504):
                        last_err = f"{host}: {msg}"
                        time.sleep(self.backoff * 2 ** attempt)
                        continue
                    raise LookupFailed(f"{host}: {msg} {data['error'].get('details') or ''}".strip())
                return data
            except requests.Timeout:
                last_err = f"{host} timed out after {self.timeout:.0f}s"
            except requests.ConnectionError as e:
                last_err = f"could not connect to {host}: {type(e).__name__}"
            except ValueError:
                raise LookupFailed(f"{host} returned non-JSON")
            time.sleep(self.backoff * 2 ** attempt)
        self.stats["errors"] += 1
        raise LookupFailed(f"{last_err} (after {self.retries + 1} attempts)")

    def download(self, url: str, dest: str) -> None:
        self._throttle(url)
        try:
            with self.session.get(url, stream=True, timeout=max(self.timeout, 120)) as r:
                r.raise_for_status()
                with open(dest + ".part", "wb") as fh:
                    for chunk in r.iter_content(1 << 16):
                        fh.write(chunk)
        except requests.RequestException as e:
            raise LookupFailed(f"download failed: {url}: {e}") from e
        import os
        os.replace(dest + ".part", dest)
