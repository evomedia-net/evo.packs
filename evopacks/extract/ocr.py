"""OCR the documents extract/text.py flagged as image-only.

PyMuPDF renders each page at 300 dpi; tesseract reads the render. The text
lands in $RAG_DOCS/_TEXT/ - the source PDF is never replaced, because
replacing it would change its sha256 and break provenance back to the
publisher's URL.

Why not ocrmypdf: it hard-depends on ghostscript, which neither winget nor
chocolatey could install on the build machine. PyMuPDF + tesseract are both
present and are enough for clean printed government documents.

Why one subprocess per file: PyMuPDF segfaults in-process on some malformed
PDFs and takes the whole interpreter with it (it did, at 10,844/19,278 during
extraction). A subprocess makes a bad file cost one child and one result row.

tesseract is single-threaded per invocation, so parallelism is files-at-once.
Resumable: a document with text already in _TEXT/ is skipped.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config

FILE_TIMEOUT = 45 * 60
MAX_PAGES = 400
DPI = 300
TESS_GUESSES = [r"C:\Program Files\Tesseract-OCR", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"]


def tesseract_exe() -> str | None:
    d = os.environ.get("TESSERACT_DIR")
    cands = ([d] if d else []) + TESS_GUESSES
    for c in cands:
        for name in ("tesseract.exe", "tesseract"):
            p = Path(c) / name
            if p.is_file():
                return str(p)
    return shutil.which("tesseract")


def check_tools() -> dict[str, str]:
    out = {}
    t = tesseract_exe()
    if t:
        r = subprocess.run([t, "--version"], capture_output=True, text=True, timeout=60)
        out["tesseract"] = (r.stdout or r.stderr).splitlines()[0]
    try:
        import fitz  # noqa: F401
        out["pymupdf"] = fitz.__doc__.split()[1] if fitz.__doc__ else "present"
    except ImportError:
        pass
    return out


def ocr_one(src: Path, dst: Path, lang: str = "eng") -> int:
    """Render + recognise one PDF. Returns chars written. Runs in a child process."""
    import fitz
    tess = tesseract_exe()
    if not tess:
        raise RuntimeError("tesseract not found (set TESSERACT_DIR)")
    parts: list[str] = []
    with fitz.open(str(src)) as doc, tempfile.TemporaryDirectory() as td:
        n = min(doc.page_count, MAX_PAGES)
        for i in range(n):
            png = Path(td) / f"p{i:04d}.png"
            doc[i].get_pixmap(dpi=DPI).save(str(png))
            r = subprocess.run([tess, str(png), "stdout", "-l", lang, "--psm", "3"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=300)
            if r.stdout.strip():
                parts.append(r.stdout.strip())
            png.unlink(missing_ok=True)
        if doc.page_count > MAX_PAGES:
            parts.append(f"[... OCR stopped at {MAX_PAGES} of {doc.page_count} pages]")
    text = "\n\n".join(parts).strip()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text + "\n", encoding="utf-8")
    return len(text)


class OCR:
    def __init__(self, root: Path | None = None, files_at_once: int = 3, lang: str = "eng"):
        self.root = root or config.rag_docs()
        self.text = self.root / "_TEXT"
        self.parallel = files_at_once
        self.lang = lang
        self.lock = threading.Lock()
        self.stats: collections.Counter = collections.Counter()
        self.results: list[dict] = []

    def candidates(self) -> list[dict]:
        p = config.state_dir() / "ocr_candidates.json"
        if p.exists():
            rows = json.loads(p.read_text(encoding="utf-8"))
        else:
            with open(self.root / "_extraction_audit.csv", encoding="utf-8-sig") as f:
                rows = [r for r in csv.DictReader(f) if r["status"] in ("none", "thin")]
        return [r for r in rows if r["relative_path"].lower().endswith(".pdf")]

    def one(self, row: dict) -> None:
        src = self.root / row["relative_path"]
        dst = self.text / Path(row["relative_path"]).with_suffix(".txt")
        if dst.exists() and dst.stat().st_size > 200:
            with self.lock:
                self.stats["already"] += 1
            return
        cmd = [sys.executable, "-m", "evopacks.extract.ocr", "--one", str(src), str(dst),
               "--lang", self.lang]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=FILE_TIMEOUT)
            chars = dst.stat().st_size if dst.exists() else 0
            if r.returncode == 0 and chars >= 200:
                status, note = "ok", ""
            else:
                status = "fail" if r.returncode else "empty"
                note = (r.stderr or "")[-200:].strip()
                if dst.exists() and chars < 200:
                    dst.unlink()
        except subprocess.TimeoutExpired:
            status, note, chars = "timeout", f"> {FILE_TIMEOUT}s", 0
        except Exception as e:                           # noqa: BLE001
            status, note, chars = "error", f"{type(e).__name__}: {str(e)[:120]}", 0
        with self.lock:
            self.stats[status] += 1
            self.results.append({"relative_path": row["relative_path"], "status": status,
                                 "chars": chars, "note": note})

    def run(self) -> dict:
        rows = self.candidates()
        print(f"OCR candidates: {len(rows)}", flush=True)
        with ThreadPoolExecutor(max_workers=self.parallel) as ex:
            list(ex.map(self.one, rows))
        out = config.state_dir() / "ocr_results.csv"
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["relative_path", "status", "chars", "note"])
            w.writeheader(); w.writerows(self.results)
        return {"candidates": len(rows), **dict(self.stats), "results": str(out)}


if __name__ == "__main__":                       # child-process entry point
    ap = argparse.ArgumentParser()
    ap.add_argument("--one", nargs=2, metavar=("SRC_PDF", "DST_TXT"), required=True)
    ap.add_argument("--lang", default="eng")
    a = ap.parse_args()
    n = ocr_one(Path(a.one[0]), Path(a.one[1]), a.lang)
    print(n)
