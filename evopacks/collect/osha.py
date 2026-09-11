"""OSHA standard interpretations and enforcement directives.

Two corpora, one collector, because they share the enumeration trick and the
rate-limit lesson:

* Neither OSHA's paginated listing nor its sitemap is complete on its own.
  The listing had 5,849 interpretations; the sitemap added 92 it omits. The
  directive listing shows only 377 *current* directives; the sitemap added 236
  archived ones. Always union the two.
* `?page=N` on the listings can return page 0 over and over. That is a
  CloudFront cache hit, not broken pagination - every listing fetch here sends
  Cache-Control: no-cache.
* osha.gov's WAF blocks at roughly 5 req/s. config.HOSTS pins it to ~2.

Directive PDFs - the defect that shaped this code:
  Stub pages read "This directive is currently only available in: PDF". The
  first recovery pass took the first .pdf link on the page and fetched the same
  file 205 times, because every osha.gov page carries a site-wide nav link to
  /sites/default/files/publications/osha2254.pdf before any content link.
  Distinct names + valid %PDF + plausible sizes passed every check; only the
  text-dedupe pass caught it. This collector selects ONLY links under
  /sites/default/files/enforcement/directives/ and prefers a filename echoing
  the directive id.

Interpretation letters are written clean: the 549-char generic disclaimer
(identical in 100% of letters) is dropped from the body and recorded in the
header; the "OSHA Archive Document" notice (27% of letters) is promoted to an
`Archived:` header field AND left in the body, because it is compliance
signal - a chunk from the middle of a 1978 letter must still carry it.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config, http

BASE = "https://www.osha.gov"
INTERP_LIST = f"{BASE}/laws-regs/interpretations?page={{}}"          # canonical; the
INTERP_PAGES = 294                                                    # /standardinterpretations
DIRECTIVE_LIST = f"{BASE}/enforcement/directives/search?page={{}}"    # path 302s here
DIRECTIVE_PAGES = 25

DISCLAIMER_START = "OSHA requirements are set by statute, standards and regulations."
DISCLAIMER_END = "https://www.osha.gov"
DISCLAIMER_FULL = (
    "OSHA requirements are set by statute, standards and regulations. Our interpretation "
    "letters explain these requirements and how they apply to particular circumstances, but "
    "they cannot create additional employer obligations. This letter constitutes OSHA's "
    "interpretation of the requirements discussed. Note that our enforcement guidance may be "
    "affected by changes to OSHA rules. Also, from time to time we update our guidance in "
    "response to new information. To keep apprised of such developments, you can consult "
    "OSHA's website at https://www.osha.gov")
ARCHIVE = "NOTICE: This is an OSHA Archive Document"
SEP = "-" * 72

ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
A = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
STD = re.compile(r'/laws-regs/regulations/standardnumber/[^"]*?">([^<]+)</a>')
DIRECTIVE_PDF = re.compile(
    r'href="(/sites/default/files/enforcement/directives/[^"]+\.pdf[^"]*)"', re.I)


def slug(s: str, n: int = 80) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.U)
    return re.sub(r"[\s_]+", "-", s).strip("-")[:n].rstrip("-") or "untitled"


def cell(td: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", td))).strip()


def article_text(h: str) -> str:
    i = h.find("<article"); j = h.find("</article>", i)
    b = h[i:j] if i >= 0 and j > i else h
    b = re.sub(r"<script.*?</script>|<style.*?</style>", " ", b, flags=re.S)
    b = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", b)
    b = re.sub(r"</td>|</th>", " | ", b)
    b = re.sub(r"<[^>]+>", " ", b)
    b = html.unescape(b).replace("\xa0", " ")
    b = re.sub(r"[ \t]+", " ", b)
    b = re.sub(r" *\n *", "\n", b)
    return re.sub(r"\n{3,}", "\n\n", b).strip()


def page_title(h: str) -> str:
    m = re.search(r"<title>([^<]*)</title>", h)
    t = html.unescape(m.group(1)).strip() if m else ""
    return re.sub(r"\s*\|\s*Occupational Safety and Health Administration\s*$", "", t).strip()


def sitemap_urls() -> list[str]:
    raw = http.get_cached(f"{BASE}/sitemap.xml", "osha_sitemap.xml", min_bytes=100_000)
    return sorted(set(re.findall(r"<loc>([^<]+)</loc>", raw or "")))


def clean_letter_body(body: str) -> tuple[str, bool]:
    """Strip the generic disclaimer and the duplicated Standard-Number preamble.
    Returns (body, archived). Idempotent."""
    i = body.find(DISCLAIMER_START)
    if i != -1:
        j = body.find(DISCLAIMER_END, i)
        end = (j + len(DISCLAIMER_END)) if j != -1 else i + 600
        while end < len(body) and body[end] in " . ":
            end += 1
        body = body[:i] + body[end:]
    m = re.match(r"\s*Standard Number:\s*\n+((?:\s*[\w.,()\[\]/\- ]+\n+)*)", body)
    if m:
        body = body[m.end():]
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, ARCHIVE in body


# ---------------------------------------------------------------- interpretations
def enumerate_interpretations() -> dict[str, dict]:
    out: dict[str, dict] = {}

    def page(p: int) -> None:
        h = http.get_cached(INTERP_LIST.format(p), f"osha_interp_{p:03d}.html", no_cache=True)
        for row in ROW.findall(h or ""):
            tds = TD.findall(row)
            if len(tds) < 2:
                continue
            am = A.search(tds[1])
            if not am or "/standardinterpretations/" not in am.group(1):
                continue
            url = BASE + html.unescape(am.group(1))
            out[url] = {"date": cell(tds[0]), "title": cell(am.group(2)),
                        "standards": cell(tds[2]) if len(tds) > 2 else ""}

    with ThreadPoolExecutor(max_workers=config.policy_for(BASE).workers) as ex:
        list(ex.map(page, range(INTERP_PAGES)))
    for u in sitemap_urls():
        if "/laws-regs/standardinterpretations/" in u and re.search(r"/\d{4}-\d{2}-\d{2}(-\d+)?$", u):
            out.setdefault(u, {})
    return out


def collect_interpretations(root: Path | None = None) -> dict:
    root = root or config.rag_docs()
    dest = root / "OSHA-Interpretations" / "Letters"
    dest.mkdir(parents=True, exist_ok=True)
    done = {m.group(1) for f in dest.iterdir() if (m := re.search(r"__([\d\-]+)\.txt$", f.name))}
    meta = enumerate_interpretations()
    todo = [u for u in sorted(meta) if u.rstrip("/").rsplit("/", 1)[-1] not in done]
    print(f"interpretations: {len(meta)} known, {len(done)} on disk, {len(todo)} to fetch", flush=True)
    fails: list[str] = []

    def one(u: str) -> None:
        ident = u.rstrip("/").rsplit("/", 1)[-1]
        m = meta.get(u, {})
        h = http.get(u)
        if not h:
            fails.append(u); return
        title = m.get("title") or page_title(h)
        body = article_text(h)
        if len(body) < 200:
            fails.append(u); return
        stds = m.get("standards") or ", ".join(dict.fromkeys(STD.findall(h)))
        date = m.get("date", "")
        if not date and (dm := re.match(r"(\d{4})-(\d{2})-(\d{2})", ident)):
            date = f"{dm.group(2)}/{dm.group(3)}/{dm.group(1)}"
        body, archived = clean_letter_body(body)
        head = (f"Title: {title}\nDate: {date}\nStandard Number: {stds}\nSource: {u}\n"
                f"Type: OSHA Standard Interpretation\n"
                f"Archived: {'Yes - may no longer represent OSHA policy' if archived else 'No'}\n"
                f"Disclaimer: Standard OSHA interpretation disclaimer removed from body "
                f"(applies to every letter; full text in README)\n")
        (dest / f"{slug(title)}__{ident}.txt").write_text(head + SEP + "\n\n" + body + "\n",
                                                          encoding="utf-8")

    with ThreadPoolExecutor(max_workers=config.policy_for(BASE).workers) as ex:
        list(ex.map(one, todo))
    (config.state_dir() / "osha_interp_fails.json").write_text(json.dumps(fails, indent=1))
    return {"known": len(meta), "fetched": len(todo) - len(fails), "failed": len(fails),
            **write_interpretations_manifest(root)}


def write_interpretations_manifest(root: Path) -> dict:
    d = root / "OSHA-Interpretations"
    rows = []
    for p in sorted((d / "Letters").glob("*.txt")):
        h = _header(p)
        ident = p.stem.rsplit("__", 1)[-1]
        iso = ""
        if (m := re.match(r"(\d{2})/(\d{2})/(\d{4})", h.get("Date", ""))):
            iso = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        elif (m := re.match(r"\d{4}-\d{2}-\d{2}", ident)):
            iso = m.group(0)
        rows.append({"relative_path": f"Letters/{p.name}", "title": h.get("Title", ""),
                     "date": iso, "standards_cited": h.get("Standard Number", ""),
                     "identifier": ident, "source_url": h.get("Source", ""),
                     "publisher": "U.S. Occupational Safety and Health Administration",
                     "bytes": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    _write_csv(d / "manifest.csv", rows)
    return {"manifest_rows": len(rows)}


# -------------------------------------------------------------------- directives
def enumerate_directives() -> dict[str, dict]:
    out: dict[str, dict] = {}

    def page(p: int) -> None:
        h = http.get_cached(DIRECTIVE_LIST.format(p), f"osha_dir_{p:03d}.html", no_cache=True)
        for row in ROW.findall(h or ""):
            tds = TD.findall(row)
            if len(tds) < 2:
                continue
            am = A.search(tds[1])
            if not am:
                continue
            u = html.unescape(am.group(1))
            u = (BASE + u) if u.startswith("/") else u.replace("http://www.osha.gov", BASE)
            out[u] = {"date": cell(tds[0]), "title": cell(am.group(2)),
                      "number": cell(tds[2]) if len(tds) > 2 else ""}

    with ThreadPoolExecutor(max_workers=config.policy_for(BASE).workers) as ex:
        list(ex.map(page, range(DIRECTIVE_PAGES)))
    for u in sitemap_urls():
        tail = u.rstrip("/").rsplit("/", 1)[-1]
        if "/enforcement/directives/" in u and len(tail) > 2 and tail != "directives":
            out.setdefault(u, {})
    return out


def directive_pdf_link(h: str, ident: str) -> str | None:
    """The directive's own PDF - never the site-wide nav link to osha2254.pdf."""
    links = [html.unescape(x) for x in DIRECTIVE_PDF.findall(h)]
    if not links:
        return None
    key = ident.replace("-", "").lower()
    links.sort(key=lambda u: 0 if key in u.rsplit("/", 1)[-1].replace("_", "").replace("-", "").lower() else 1)
    return BASE + links[0]


def collect_directives(root: Path | None = None) -> dict:
    root = root or config.rag_docs()
    d = root / "OSHA-Directives"
    txt, pdf = d / "Text", d / "PDF"
    txt.mkdir(parents=True, exist_ok=True); pdf.mkdir(exist_ok=True)
    done = {re.sub(r"\.(txt|pdf)$", "", f.name).rsplit("__", 1)[-1]
            for sub in (txt, pdf) for f in sub.iterdir()}
    meta = enumerate_directives()
    todo = [u for u in sorted(meta)
            if re.sub(r"\.pdf$", "", u.rstrip("/").rsplit("/", 1)[-1], flags=re.I) not in done]
    print(f"directives: {len(meta)} known, {len(done)} on disk, {len(todo)} to fetch", flush=True)
    unavailable: list[str] = []

    def one(u: str) -> None:
        m = meta.get(u, {})
        ident = re.sub(r"\.pdf$", "", u.rstrip("/").rsplit("/", 1)[-1], flags=re.I)
        title = m.get("title") or ident
        if u.lower().endswith(".pdf"):
            http.download(u, pdf / f"{slug(title)}__{ident}.pdf", expect_ext=".pdf")
            return
        h = http.get(u)
        if not h:
            unavailable.append(ident); return
        title = m.get("title") or page_title(h)
        body = article_text(h)
        (txt / f"{slug(title)}__{ident}.txt").write_text(
            f"Title: {title}\nDate: {m.get('date', '')}\nDirective Number: {m.get('number', '')}\n"
            f"Source: {u}\nType: OSHA Enforcement Directive\n{SEP}\n\n{body}\n", encoding="utf-8")
        # stub page -> the real document is PDF-only
        if len(body) < 600 or "available in" in body and "PDF" in body[-200:]:
            link = directive_pdf_link(h, ident)
            if link:
                http.download(link, pdf / f"{slug(title)}__{ident}.pdf", expect_ext=".pdf")
            else:
                unavailable.append(ident)     # "Electronic text not currently available"

    with ThreadPoolExecutor(max_workers=config.policy_for(BASE).workers) as ex:
        list(ex.map(one, todo))
    (config.state_dir() / "osha_directives_unavailable.json").write_text(json.dumps(unavailable, indent=1))
    return {"known": len(meta), "unavailable": len(unavailable), **write_directives_manifest(root)}


def write_directives_manifest(root: Path) -> dict:
    d = root / "OSHA-Directives"
    rows = []
    for sub, kind in (("Text", "TEXT"), ("PDF", "PDF")):
        for p in sorted((d / sub).iterdir()):
            ident = p.stem.rsplit("__", 1)[-1]
            if kind == "TEXT":
                h = _header(p)
                title, date, num, src = (h.get("Title", ""), h.get("Date", ""),
                                         h.get("Directive Number", ""), h.get("Source", ""))
            else:
                title, date, num = p.stem.rsplit("__", 1)[0].replace("-", " "), "", ""
                src = f"{BASE}/sites/default/files/enforcement/directives/{ident}.pdf"
            iso = ""
            if (m := re.match(r"(\d{2})/(\d{2})/(\d{4})", date)):
                iso = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
            rows.append({"relative_path": f"{sub}/{p.name}", "format": kind, "title": title,
                         "date": iso, "directive_number": num, "identifier": ident,
                         "source_url": src,
                         "publisher": "U.S. Occupational Safety and Health Administration",
                         "bytes": p.stat().st_size,
                         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    _write_csv(d / "manifest.csv", rows)
    return {"manifest_rows": len(rows)}


# ----------------------------------------------------------------------- helpers
def _header(p: Path) -> dict[str, str]:
    out = {}
    with open(p, encoding="utf-8") as f:
        for _ in range(10):
            ln = f.readline()
            if not ln or ln.startswith("---"):
                break
            if ":" in ln:
                k, v = ln.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
