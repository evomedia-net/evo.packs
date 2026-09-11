"""NFPA's free public-education material and open-access research.

Only what NFPA gives away: safety tip sheets, lesson plans, fact sheets,
checklists (English and Spanish), and Fire Protection Research Foundation +
NFPA Research reports. NFPA's licensed standards are NOT collected - they are
out of scope entirely.

Discovery is via the sitemap NFPA's own robots.txt advertises. The landing
pages under /downloadable-resources/ and /education-and-research/research/
are permitted; the PDFs are served from /-/media/, which robots disallows for
crawlers. This fetches from an enumerated list of ~470 known landing pages at
a throttled rate - it is not a crawl of the asset tree. If a stricter posture
is wanted, NFPA sells a content licence.

The Spanish edition of a page lives at /es/<same path>. A "Spanish" link that
is byte-identical to the English one is the same asset, not a translation.

Keep the full asset URL including ?rev=<hash>: some assets return HTTP 500
without it.
"""
from __future__ import annotations

import csv
import hashlib
import html
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config, http

BASE = "https://www.nfpa.org"
DOC_EXT = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".zip")


def slug(s: str, n: int = 85) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.U)
    return re.sub(r"[\s_]+", "-", s).strip("-")[:n].rstrip("-") or "untitled"


def _files(h: str) -> list[str]:
    """Every /-/media/ document reference on a page, full URL with query kept."""
    out: list[str] = []
    for m in re.finditer(r"/-/media/", h):
        seg = h[m.start():m.start() + 400]
        cut = min([seg.find(c) for c in ('"', "'", "\\", " ", "<", ">", ")") if seg.find(c) != -1]
                  or [len(seg)])
        url = html.unescape(seg[:cut])
        base = url.split("?")[0]
        if base.lower().endswith(DOC_EXT) and "/Images/" not in base and "/Icons/" not in base \
                and url not in out:
            out.append(url)
    return out


def _title(h: str) -> str:
    m = re.search(r"<title>([^<]*)</title>", h)
    t = html.unescape(m.group(1)).strip() if m else ""
    t = re.sub(r"\s*\|\s*NFPA\s*$", "", t)
    return re.sub(r"\.?\s*Download the free NFPA (PDF|resource)\.?\s*$", "", t, flags=re.I).strip(" .\n")


def enumerate_pages() -> list[dict]:
    sm = http.get_cached(f"{BASE}/sitemap.xml", "nfpa_sitemap.xml", min_bytes=10_000)
    urls = sorted(set(re.findall(r"<loc>([^<]+)</loc>", sm or "")))
    pages = []
    for u in urls:
        if "/downloadable-resources/" in u:
            parts = [x for x in u.replace(BASE, "").split("/") if x]
            if parts[1] == "social-media":          # image assets, not documents
                continue
            pages.append({"url": u, "kind": "Public-Education", "category": parts[1]})
        elif "/education-and-research/research/" in u:
            pages.append({"url": u, "kind": "Research-Reports",
                          "category": "FPRF" if "fire-protection-research-foundation" in u else "NFPA-Research"})
    return pages


def harvest(pages: list[dict]) -> list[dict]:
    items: dict[tuple[str, str], dict] = {}

    def one(pg: dict) -> None:
        h = http.get_cached(pg["url"], http.cache_key(pg["url"]))
        if not h:
            return
        title = _title(h)
        en = _files(h)
        es_page = BASE + "/es" + pg["url"].replace(BASE, "")
        h2 = http.get_cached(es_page, http.cache_key(es_page)) if en else None
        es = [f for f in _files(h2 or "") if f.split("?")[0] not in {x.split("?")[0] for x in en}]
        for lang, files, page in (("English", en, pg["url"]), ("Spanish", es, es_page)):
            for f in files:
                items.setdefault((f.split("?")[0], lang),
                                 {"url": BASE + f, "language": lang, "title": title,
                                  "kind": pg["kind"], "category": pg["category"], "page": page})

    with ThreadPoolExecutor(max_workers=config.policy_for(BASE).workers) as ex:
        list(ex.map(one, pages))
    return list(items.values())


def collect(root: Path | None = None) -> dict:
    root = root or config.rag_docs()
    dest = root / "NFPA-Public"
    pages = enumerate_pages()
    print(f"nfpa-public: {len(pages)} landing pages", flush=True)
    items = harvest(pages)
    print(f"nfpa-public: {len(items)} file/language records", flush=True)
    seen: dict[str, int] = {}
    for it in items:
        base = it["url"].split("?")[0].rsplit("/", 1)[-1]
        p = dest / it["kind"] / ("English" if it["language"] == "English" else "Espanol") \
            / f"{slug(it['title'])}__{Path(base).stem}{Path(base).suffix.lower()}"
        k = str(p).lower()
        if k in seen:
            seen[k] += 1
            p = p.with_name(f"{p.stem}-{seen[k] - 1}{p.suffix}")
        else:
            seen[k] = 1
        it["path"] = p

    def one(it: dict) -> None:
        http.download(it["url"], it["path"], expect_ext=it["path"].suffix.lower())

    with ThreadPoolExecutor(max_workers=config.policy_for(BASE).workers) as ex:
        list(ex.map(one, items))
    return write_manifest(root, items)


def write_manifest(root: Path, items: list[dict]) -> dict:
    d = root / "NFPA-Public"
    rows = []
    for it in sorted(items, key=lambda x: str(x["path"])):
        p = it["path"]
        if not p.exists():
            continue
        rows.append({"relative_path": p.relative_to(d).as_posix(), "collection": it["kind"],
                     "category": it["category"], "language": it["language"],
                     "format": p.suffix.lstrip(".").upper(), "title": it["title"],
                     "source_url": it["url"], "source_page": it["page"],
                     "publisher": "National Fire Protection Association",
                     "bytes": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    with open(d / "manifest.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    return {"records": len(items), "on_disk": len(rows), "missing": len(items) - len(rows)}
