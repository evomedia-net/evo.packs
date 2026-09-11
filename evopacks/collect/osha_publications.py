"""OSHA's publications library - fact sheets, QuickCards, guides, posters.

Enumerated from OSHA's own complete index, /publications/all (25 pages of 50)
plus /publications/all-n for numerically-titled items. Each title heading is
followed by one `views-row` per LANGUAGE VARIANT, with the language in a
`<b lang=..>` marker - that marker is the reliable language source. The
per-link `title` attribute is inconsistent and misfiled ~360 documents in an
early attempt.

Coverage was verified against all 226 of OSHA's by-language, by-type and
by-topic index pages: that sweep found 1,250 asset URLs, every one already
here. OSHA titles every translation in English, so a Spanish PDF carries an
English filename - the `language` column is authoritative.

Layout: English/, Espanol/, Other-Languages/<Language>/. A bilingual PDF
(English page 1, Spanish page 2) is filed under both languages on purpose;
index/dedupe.py flags the second copy embed=no.
"""
from __future__ import annotations

import csv
import hashlib
import html
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from .. import config, http

BASE = "https://www.osha.gov"
PAGES = [f"{BASE}/publications/all?page={p}" for p in range(25)]
PAGES.append(f"{BASE}/publications/all-n?pub_first_letter_filter=1")

ROW_SPLIT = '<div class="view-id-publications-all-views-listing views-row">'
LANG = re.compile(r"<b lang=([^>]*?)>(.*?):</b>", re.S)
PUBID = re.compile(r'views-field-field-publication-id"><span class="field-content">\((.*?)\)</span>', re.S)
ANCHOR = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
KEEP_FMT = {"PDF", "HTML", "EPUB", "MOBI", "DOCX"}
INDEX_PATHS = re.compile(r"^/publications/(all|all-n|cart|bytype|bytopic|bylanguage|add|"
                         r"fatal-facts|maritime-publications)(/|$|\?)")
EXT = {"PDF": ".pdf", "EPUB": ".epub", "MOBI": ".mobi", "DOCX": ".docx", "HTML": ".html"}


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ").strip()


def slug(s: str, n: int = 90) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.U)
    return re.sub(r"[\s_]+", "-", s).strip("-")[:n].rstrip("-") or "untitled"


def _language(code: str, label: str) -> str:
    code = code.strip().strip('"').lower()
    if code == "english" or label.lower() == "english":
        return "English"
    if code == "es" or label.lower() == "spanish":
        return "Spanish"
    m = re.search(r"\(([^)]+)\)\s*$", label)       # 'Tiếng Việt(Vietnamese)'
    return (m.group(1) if m else label).strip() or "Unknown"


def _is_asset(fmt: str, url: str) -> bool:
    if fmt not in KEEP_FMT:
        return False
    u = urlparse(url)
    if u.netloc != "www.osha.gov" or u.query:
        return False
    if u.path.startswith("/sites/default/files/"):
        return True
    return u.path.startswith("/publications/") and not INDEX_PATHS.match(u.path) \
        and len(u.path) > len("/publications/")


def enumerate_catalog() -> list[dict]:
    items: dict[tuple, dict] = {}
    for i, url in enumerate(PAGES):
        # CloudFront serves page 0 for every ?page=N unless told not to cache
        h = http.get_cached(url, f"osha_pubs_{i:02d}.html", no_cache=True)
        if not h:
            continue
        chunks = re.split(r"<h3><h5>(.*?)</h5></h3>", h, flags=re.S)
        for k in range(1, len(chunks) - 1, 2):
            title, body = _clean(chunks[k]), chunks[k + 1]
            for row in body.split(ROW_SPLIT)[1:]:
                lm = LANG.search(row)
                lang = _language(html.unescape(lm.group(1)), _clean(lm.group(2))) if lm else "Unknown"
                pm = PUBID.search(row)
                pubid = _clean(pm.group(1)) if pm else ""
                for href, text in ANCHOR.findall(row):
                    fmt = _clean(text)
                    href = html.unescape(href)
                    full = (href if href.startswith("http") else BASE + href).split("#")[0]
                    if _is_asset(fmt, full):
                        items.setdefault((full, lang), {"url": full, "language": lang, "format": fmt,
                                                        "title": title, "publication_id": pubid})
    return list(items.values())


def _folder(root: Path, lang: str) -> Path:
    if lang == "English":
        return root / "EHS" / "English"
    if lang == "Spanish":
        return root / "EHS" / "Espanol"
    return root / "EHS" / "Other-Languages" / (re.sub(r"[^\w\-]+", "-", lang).strip("-") or "Unknown")


def collect(root: Path | None = None) -> dict:
    root = root or config.rag_docs()
    items = enumerate_catalog()
    print(f"publications: {len(items)} file/language records", flush=True)
    seen: dict[str, int] = {}
    for it in items:
        base = it["url"].rstrip("/").rsplit("/", 1)[-1]
        stem, ext = Path(base).stem, Path(base).suffix.lower()
        if not ext or len(ext) > 6:
            stem, ext = base, EXT.get(it["format"], ".html")
        p = _folder(root, it["language"]) / f"{slug(it['title'])}__{stem}{ext}"
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
    d = root / "EHS"
    rows = []
    for it in sorted(items, key=lambda x: str(x["path"])):
        p = it["path"]
        if not p.exists():
            continue
        rows.append({"relative_path": p.relative_to(d).as_posix(), "language": it["language"],
                     "format": it["format"], "title": it["title"],
                     "publication_id": it["publication_id"], "source_url": it["url"],
                     "bytes": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    with open(d / "manifest.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    return {"records": len(items), "on_disk": len(rows), "missing": len(items) - len(rows)}
