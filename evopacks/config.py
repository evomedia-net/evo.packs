"""Paths and per-host politeness settings.

Nothing here is machine-specific. The corpus root comes from $RAG_DOCS (or
--root on the CLI); examples in the docs use C:\\path\\to\\RAG-Docs and the
default is ./RAG-Docs relative to the working directory.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def rag_docs() -> Path:
    return Path(os.environ.get("RAG_DOCS", "RAG-Docs")).resolve()


def text_dir() -> Path:
    return rag_docs() / "_TEXT"


def state_dir() -> Path:
    """Per-run state (URL lists, retry queues, fetch logs). Regenerable; gitignored."""
    p = Path(os.environ.get("EVOPACKS_STATE", "state")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    """Cached listing/HTML pages so an enumeration can be re-parsed without refetching."""
    p = Path(os.environ.get("EVOPACKS_CACHE", ".cache")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class HostPolicy:
    """How hard a source may be hit. Every number here was learned the expensive way."""
    workers: int
    delay: float          # seconds each worker pauses after a successful request
    block_backoff: float  # seconds to pause EVERY worker on a 403/429/503
    note: str = ""


HOSTS: dict[str, HostPolicy] = {
    # CloudFront WAF: a 6-worker unthrottled run was blocked after ~2,900
    # requests and returned 403 for the next 3,587. ~2 req/s is safe.
    "www.osha.gov": HostPolicy(2, 0.9, 300.0,
                               "WAF blocks above ~5 req/s; listings need Cache-Control: no-cache"),
    # Zero pushback across 9,000 pages at 5 workers; first 403 seen at 12.
    "www.epa.gov": HostPolicy(8, 0.1, 60.0,
                              "sitemap has NO pdfs - harvest links from pages; keep ?rev= on assets"),
    # Official JSON/XML API. Seven part-level calls beat 812 per-section ones.
    "www.ecfr.gov": HostPolicy(2, 1.0, 60.0,
                               "full-text endpoint returns 406 without Accept-Encoding"),
    "www.nfpa.org": HostPolicy(4, 0.25, 120.0,
                               "robots disallows /-/media/ and /en/; only free downloadable-resources"),
    "archive.org": HostPolicy(4, 0.35, 120.0, "metadata API; be polite"),
}

DEFAULT_POLICY = HostPolicy(2, 0.5, 120.0)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def policy_for(url: str) -> HostPolicy:
    host = url.split("/")[2].lower() if "://" in url else ""
    for h, p in HOSTS.items():
        if host == h or host.endswith("." + h):
            return p
    return DEFAULT_POLICY


# Hosts that must never be fetched, and why. Checked by http.get().
FORBIDDEN_HOSTS = {
    # robots.txt: Crawl-delay 10, Request-rate 1/10, Visit-time window.
    # Bulk collection would take weeks and openly defy a stated policy.
    "nepis.epa.gov": "robots.txt limits to 1 request / 10 s in a visit-time window",
}
