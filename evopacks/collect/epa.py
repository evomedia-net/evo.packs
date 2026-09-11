"""EPA - the EHS slice of www.epa.gov.

Three stages, because EPA's sitemap contains ZERO PDFs: all 74,955 URLs are
HTML pages and documents are only reachable as links from those pages.

  survey   sitemap index -> 38 child sitemaps -> URLs, scoped to the EHS
           sections below (9,017 of 74,955). Excludes news releases, state
           air-quality plans, FOIA, Inspector General, permit databases, and
           enforcement *settlement* pages - individual company consent decrees
           are enforcement news, not guidance.
  harvest  every EPA-hosted document link on those pages, with link text and
           referring page. nepis.epa.gov is refused by http (robots policy).
  download organised by section. Current-host URLs first: 68 legacy http://
           links to decommissioned hosts once tied up all 12 workers before a
           single live document was fetched.

Keep the ?rev= query on assets - some return HTTP 500 without it.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import html
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config, http

BASE = "https://www.epa.gov"
SECTIONS = {
    # chemical safety / toxics
    "chemicals-under-tsca", "assessing-and-managing-chemicals-under-tsca",
    "reviewing-new-chemicals-under-toxic-substances-control-act-tsca", "chemical-data-reporting",
    "pcbs", "formaldehyde", "mercury", "greenchemistry",
    "test-guidelines-pesticides-and-toxic-substances", "sustainable-futures", "chemical-research",
    # emergency planning & response
    "epcra", "rmp", "emergency-response", "emergency-response-research", "emergencies-iaq",
    "oil-spills-prevention-and-preparedness-regulations",
    # exposure / risk
    "aegl", "risk",
    # hazardous waste
    "rcra", "hw-sw846", "ust", "coal-combustion-residuals",
    # contaminants
    "lead", "asbestos", "radon", "mold", "pfas",
    # indoor air
    "indoor-air-quality-iaq", "indoorairplus",
    # reporting / compliance / cleanup
    "toxics-release-inventory-tri-program", "compliance", "enforcement",
    "superfund", "brownfields", "cleanups", "greenercleanups",
    # worker safety / refrigerants
    "pesticide-worker-safety", "safepestcontrol", "soil-fumigants", "ozone-layer-protection",
}
SETTLEMENT = re.compile(r"settlement|consent-decree|-cd\b|administrative-settlement", re.I)
DOC = re.compile(r'<a[^>]+href="([^"]+?\.(?:pdf|docx?|xlsx?|csv|zip))(\?[^"]*)?"[^>]*>(.*?)</a>',
                 re.I | re.S)
LEGACY = ("water.epa.gov", "cfpub.epa.gov", "yosemite.epa.gov", "www3.epa.gov",
          "archive.epa.gov", "gaftp.epa.gov")
MAX_BYTES = 300 * 1024 * 1024


def slug(s: str, n: int = 80) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.U)
    return re.sub(r"[\s_]+", "-", s).strip("-")[:n].rstrip("-") or "document"


def _section(page: str) -> str:
    p = page.replace(BASE + "/", "").split("/")
    return p[0] if p and p[0] else "misc"


def survey() -> list[str]:
    idx = http.get_cached(f"{BASE}/sitemap.xml", "epa_sitemap_index.xml", min_bytes=500)
    children = re.findall(r"<loc>([^<]+)</loc>", idx or "")
    urls: set[str] = set()

    def child(u: str) -> None:
        n = re.search(r"page=(\d+)", u)
        x = http.get_cached(u, f"epa_sitemap_{n.group(1) if n else 0}.xml", min_bytes=500)
        urls.update(re.findall(r"<loc>([^<]+)</loc>", x or ""))

    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(child, children))
    scoped = []
    for u in sorted(urls):
        s = _section(u)
        if s not in SECTIONS:
            continue
        if s == "enforcement" and SETTLEMENT.search(u):
            continue
        scoped.append(u)
    (config.state_dir() / "epa_scoped_pages.json").write_text(json.dumps(scoped, indent=0))
    return scoped


def harvest(pages: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    pol = config.policy_for(BASE)

    def one(u: str) -> None:
        h = http.get_cached(u, http.cache_key(u))
        if not h:
            return
        for href, _q, text in DOC.findall(h):
            href = html.unescape(href) + (html.unescape(_q) if _q else "")
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = BASE + href
            elif not href.startswith("http"):
                continue
            host = href.split("/")[2].lower()
            if not host.endswith("epa.gov") or host in config.FORBIDDEN_HOSTS:
                continue
            key = href.split("?")[0]
            e = found.setdefault(key, {"url": href, "text": _clean(text), "pages": []})
            if len(e["pages"]) < 3:
                e["pages"].append(u)

    with ThreadPoolExecutor(max_workers=pol.workers) as ex:
        list(ex.map(one, pages))
    (config.state_dir() / "epa_doc_links.json").write_text(
        json.dumps(found, indent=1, ensure_ascii=False), encoding="utf-8")
    return found


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()[:300]


def download(links: dict[str, dict], root: Path | None = None) -> list[dict]:
    root = root or config.rag_docs()
    dest = root / "EPA"
    plan = []
    for key, meta in links.items():
        pages = meta.get("pages") or []
        sec = _section(pages[0]) if pages else "misc"
        base = key.rsplit("/", 1)[-1]
        stem, ext = Path(base).stem, (Path(base).suffix.lower() or ".pdf")
        plan.append({"url": meta["url"], "section": sec, "title": meta.get("text") or stem,
                     "ref_page": pages[0] if pages else "",
                     "path": dest / sec / f"{slug(meta.get('text') or stem)}__{slug(stem, 60)}{ext}"})
    seen: collections.Counter = collections.Counter()
    for r in plan:
        k = str(r["path"]).lower()
        seen[k] += 1
        if seen[k] > 1:
            r["path"] = r["path"].with_name(f"{r['path'].stem}-{seen[k] - 1}{r['path'].suffix}")
    plan.sort(key=lambda r: (r["url"].startswith("http://"), any(h in r["url"] for h in LEGACY)))

    def one(r: dict) -> None:
        http.download(r["url"], r["path"], expect_ext=r["path"].suffix.lower())

    with ThreadPoolExecutor(max_workers=config.policy_for(BASE).workers) as ex:
        list(ex.map(one, plan))
    return plan


def collect(root: Path | None = None) -> dict:
    root = root or config.rag_docs()
    pages = survey()
    print(f"epa: {len(pages)} scoped pages", flush=True)
    links = harvest(pages)
    print(f"epa: {len(links)} document links", flush=True)
    plan = download(links, root)
    return write_manifest(root, plan)


def write_manifest(root: Path, plan: list[dict]) -> dict:
    d = root / "EPA"
    rows = []
    for r in sorted(plan, key=lambda x: str(x["path"])):
        p = r["path"]
        if not p.exists():
            continue
        rows.append({"relative_path": p.relative_to(d).as_posix(), "section": r["section"],
                     "format": p.suffix.lstrip(".").upper(), "title": r["title"][:300],
                     "source_url": r["url"], "source_page": r["ref_page"],
                     "publisher": "U.S. Environmental Protection Agency",
                     "bytes": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    with open(d / "manifest.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    return {"links": len(plan), "on_disk": len(rows), "missing": len(plan) - len(rows),
            "sections": len({r["section"] for r in rows})}
