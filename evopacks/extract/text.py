"""Extract a text layer from every document and audit what is actually usable.

Writes a ship-ready text mirror under $RAG_DOCS/_TEXT/ and
$RAG_DOCS/_extraction_audit.csv classifying each source file:

  ok      real text layer, ready to chunk
  none    no extractable text - an image-only scan -> ocr.py
  thin    suspiciously little text for its size - likely a partial scan
  skip    not a text-bearing format here (zip, epub, mobi, doc, xls, pptx)
  error   failed to open / timed out

Why pdftotext in a subprocess and not PyMuPDF in-process: a malformed PDF
segfaulted PyMuPDF and killed a 19,000-file run at 10,844. A subprocess makes
a bad file cost one child process and one audit row.

Why the XLSX caps: spreadsheets compress ~5x, so a source-size cap alone
missed a 301 MB AMTIC monitoring dump that expanded to 50 MB of text and
stalled the run at 662 MB RAM. Rows are capped too. (Spreadsheet text is
extracted for completeness but never embedded - see index/dedupe.py.)

Idempotent: already-extracted files are skipped, so this resumes.
"""
from __future__ import annotations

import collections
import csv
import html
import json
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config

TEXT_EXT = {".txt", ".md", ".csv"}
SKIP_EXT = {".zip", ".mobi", ".epub", ".doc", ".xls", ".pptx", ".ppt"}
META_NAMES = {"manifest.csv", "readme.md", "readme.txt", "sources.md", "sources.txt",
              "master-index.csv", "_extraction_audit.csv", "_text_dedupe.csv"}
PDFTOTEXT_TIMEOUT = 120
MAX_XLSX_MB = 10
MAX_ROWS_PER_SHEET = 3000
DROP_TAGS = ("script", "style", "nav", "header", "footer", "form", "noscript", "svg")


def _clean(t: str) -> str:
    t = t.replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _pdf(p: Path) -> str:
    fd, tmp = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        subprocess.run(["pdftotext", "-q", "-enc", "UTF-8", str(p), tmp],
                       timeout=PDFTOTEXT_TIMEOUT, capture_output=True)
        return Path(tmp).read_text(encoding="utf-8", errors="replace").strip()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _html(p: Path) -> tuple[str, str]:
    import lxml.html as LH
    doc = LH.parse(str(p)).getroot()
    for el in doc.xpath("//" + " | //".join(DROP_TAGS)):
        el.getparent().remove(el)
    node = None
    for xp in ("//article", "//main", "//div[@id='main-content']"):
        got = doc.xpath(xp)
        if got:
            node = got[0]
            break
    node = node if node is not None else doc
    title = ""
    t = doc.xpath("//title/text()")
    if t:
        title = re.sub(r"\s*\|\s*Occupational Safety and Health Administration\s*$", "",
                       html.unescape(t[0])).strip()
    return title, _clean(node.text_content())


def _docx(p: Path) -> str:
    import docx
    d = docx.Document(str(p))
    parts = [par.text for par in d.paragraphs if par.text.strip()]
    for tbl in d.tables:
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return _clean("\n".join(parts))


def _xlsx(p: Path) -> str:
    import openpyxl
    if p.stat().st_size > MAX_XLSX_MB * 1024 * 1024:
        raise ValueError(f"xlsx over {MAX_XLSX_MB}MB - bulk data, not a document")
    wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            parts.append(f"## Sheet: {ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS_PER_SHEET:
                    parts.append(f"[... truncated at {MAX_ROWS_PER_SHEET} rows]")
                    break
                vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if vals:
                    parts.append(" | ".join(vals))
    finally:
        wb.close()
    return _clean("\n".join(parts))


class Extractor:
    def __init__(self, root: Path | None = None, workers: int = 8):
        self.root = root or config.rag_docs()
        self.out = self.root / "_TEXT"
        self.workers = workers
        self.lock = threading.Lock()
        self.rows: list[dict] = []
        self.stats: collections.Counter = collections.Counter()

    def _dst(self, src: Path) -> Path:
        rel = src.relative_to(self.root)
        return self.out / rel.with_suffix(".txt")

    def _record(self, src: Path, status: str, chars: int, note: str = "") -> None:
        kb = max(src.stat().st_size / 1024, 1)
        with self.lock:
            self.stats[status] += 1
            self.rows.append({"corpus": src.relative_to(self.root).parts[0],
                              "relative_path": src.relative_to(self.root).as_posix(),
                              "status": status, "chars": chars, "source_kb": round(kb),
                              "chars_per_kb": round(chars / kb, 2), "note": note})

    def one(self, src: Path) -> None:
        ext = src.suffix.lower()
        dst = self._dst(src)
        if ext in SKIP_EXT:
            self._record(src, "skip", 0, f"{ext} not text-extractable here")
            return
        # Existence alone is not currency (evo.packs#7). This skipped any file
        # whose mirror merely EXISTED, so re-collecting a corpus never reached
        # _TEXT and `build` packaged the previous capture while reporting a new
        # version - a rebuild that looks done and ships the old text. Caught
        # rebuilding 49 CFR: the corpus grew from 522 KB to 1.7 MB and the pack
        # came out byte-identical. Re-extract when the source is newer.
        if (dst.exists() and dst.stat().st_size > 0
                and dst.stat().st_mtime >= src.stat().st_mtime):
            with self.lock:
                self.stats["already"] += 1
            self._record(src, "ok", len(dst.read_text(encoding="utf-8", errors="replace")),
                         "previously extracted")
            return
        try:
            title, body = "", ""
            if ext in TEXT_EXT:
                body = src.read_text(encoding="utf-8", errors="replace")
            elif ext == ".pdf":
                body = _pdf(src)
                kb = max(src.stat().st_size / 1024, 1)
                if len(body) < 200:
                    self._record(src, "none", len(body), "no text layer - needs OCR")
                    return
                if len(body) / kb < 1.5:
                    self._record(src, "thin", len(body), "very low text density - likely partial scan")
                    return
            elif ext in (".html", ".htm"):
                title, body = _html(src)
            elif ext == ".docx":
                body = _docx(src)
            elif ext == ".xlsx":
                body = _xlsx(src)
            else:
                self._record(src, "skip", 0, f"unhandled {ext}")
                return
            if len(body.strip()) < 40:
                self._record(src, "thin", len(body), "nearly empty")
                return
            dst.parent.mkdir(parents=True, exist_ok=True)
            head = f"Title: {title}\n{'-' * 72}\n\n" if title else ""
            dst.write_text(head + body + "\n", encoding="utf-8")
            self._record(src, "ok", len(body))
        except ValueError as e:                      # deliberate size skip
            self._record(src, "skip", 0, str(e))
        except subprocess.TimeoutExpired:
            self._record(src, "error", 0, "pdftotext timeout")
        except Exception as e:                       # noqa: BLE001 - one bad file must not stop the run
            self._record(src, "error", 0, f"{type(e).__name__}: {str(e)[:70]}")

    def run(self) -> dict:
        jobs = []
        for r, dirs, fs in os.walk(self.root):
            dirs[:] = [d for d in dirs if d != "_TEXT"]
            for f in fs:
                if f.lower() in META_NAMES or f.endswith(".part"):
                    continue
                jobs.append(Path(r) / f)
        print(f"documents to process: {len(jobs)}", flush=True)
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(self.one, jobs))

        self.rows.sort(key=lambda r: (r["corpus"], r["relative_path"]))
        with open(self.root / "_extraction_audit.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()))
            w.writeheader(); w.writerows(self.rows)

        need = [r for r in self.rows if r["status"] in ("none", "thin")]
        (config.state_dir() / "ocr_candidates.json").write_text(
            json.dumps(need, indent=1), encoding="utf-8")
        by = collections.defaultdict(collections.Counter)
        for r in self.rows:
            by[r["corpus"]][r["status"]] += 1
        return {"total": len(self.rows), "by_status": dict(self.stats),
                "by_corpus": {c: dict(v) for c, v in by.items()}, "ocr_candidates": len(need)}
