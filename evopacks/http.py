"""One polite HTTP layer for every collector.

Encodes the behaviour that every scratchpad fetcher grew independently:

* per-host worker count and inter-request delay (config.HOSTS)
* a 403/429/503 pauses EVERY worker for that host, not just the one that saw
  it - a WAF block is a property of the source IP, so racing on with the
  other workers only extends it
* short connect timeout so a dead legacy host costs seconds, not minutes -
  68 http:// links to decommissioned EPA hosts once stalled all 12 workers
  before a single live document was fetched
* magic-byte check on binary fetches, because an HTML error page saved as
  .pdf passes every size check
* optional disk cache for listing pages, so enumeration can be re-parsed
  without refetching
"""
from __future__ import annotations

import hashlib
import random
import threading
import time
from pathlib import Path

import requests

from . import config

MAGIC = {".pdf": b"%PDF", ".docx": b"PK", ".xlsx": b"PK", ".zip": b"PK", ".epub": b"PK"}

_lock = threading.Lock()
_throttle_until: dict[str, float] = {}     # host -> epoch seconds
_stats: dict[str, int] = {}


def _bump(key: str, n: int = 1) -> None:
    with _lock:
        _stats[key] = _stats.get(key, 0) + n


def stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",   # eCFR returns 406 without this
    })
    return s


_session = session()


def _host(url: str) -> str:
    return url.split("/")[2].lower() if "://" in url else ""


def _wait_if_throttled(host: str) -> None:
    while True:
        with _lock:
            until = _throttle_until.get(host, 0.0)
        left = until - time.time()
        if left <= 0:
            return
        time.sleep(min(left, 120))


def _throttle(host: str, seconds: float) -> None:
    with _lock:
        _throttle_until[host] = max(_throttle_until.get(host, 0.0), time.time() + seconds)


def get(url: str, *, binary: bool = False, no_cache: bool = False,
        attempts: int = 5, timeout: tuple[float, float] = (8, 60),
        expect_ext: str | None = None) -> bytes | str | None:
    """Fetch with host-aware backoff. Returns text (utf-8) or bytes, or None.

    None means: gone (404/410), forbidden host, or retries exhausted. The
    caller decides whether that is a failure worth recording.
    """
    host = _host(url)
    if host in config.FORBIDDEN_HOSTS:
        _bump("forbidden")
        return None
    pol = config.policy_for(url)
    headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"} if no_cache else {}

    for a in range(attempts):
        _wait_if_throttled(host)
        try:
            r = _session.get(url, timeout=timeout, headers=headers, stream=binary)
            if r.status_code == 200:
                if binary:
                    data = r.content
                    if expect_ext and expect_ext in MAGIC and not data.startswith(MAGIC[expect_ext]):
                        _bump("wrong_type")
                        return None
                    _bump("ok")
                    time.sleep(pol.delay)
                    return data
                r.encoding = "utf-8"
                _bump("ok")
                time.sleep(pol.delay)
                return r.text
            if r.status_code in (403, 429, 503):
                back = pol.block_backoff * (a + 1) + random.uniform(0, 10)
                _throttle(host, back)
                _bump("throttled")
                continue
            if r.status_code in (404, 410):
                _bump("gone")
                return None
            _bump(f"http_{r.status_code}")
        except requests.RequestException:
            _bump("exception")
            if a >= 1:                 # dead host: stop burning the worker on it
                return None
        time.sleep(2 * (a + 1))
    _bump("exhausted")
    return None


def get_cached(url: str, name: str, *, no_cache: bool = False, min_bytes: int = 3000,
               refresh: bool = False) -> str | None:
    """Text fetch backed by the disk cache. `name` is the cache key (a filename).

    refresh=True bypasses and overwrites the cache - the freshness checker
    must see today's listing, not the one the corpus was built from.
    """
    p = config.cache_dir() / name
    if not refresh and p.exists() and p.stat().st_size >= min_bytes:
        return p.read_text(encoding="utf-8")
    t = get(url, no_cache=no_cache)
    if t:
        p.write_text(t, encoding="utf-8")
    return t


def cache_key(url: str, suffix: str = ".html") -> str:
    return hashlib.md5(url.encode()).hexdigest() + suffix


def download(url: str, dest: Path, *, expect_ext: str | None = None) -> int:
    """Download to dest atomically (via .part). Returns bytes written, 0 on failure.

    Skips if dest already exists and is non-empty - this is what makes every
    collector resumable.
    """
    if dest.exists() and dest.stat().st_size > 0:
        _bump("skip_existing")
        return dest.stat().st_size
    ext = expect_ext or dest.suffix.lower()
    data = get(url, binary=True, expect_ext=ext, timeout=(8, 180))
    if not data:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return len(data)
