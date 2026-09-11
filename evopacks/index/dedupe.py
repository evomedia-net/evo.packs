"""Build the embed index over the extracted text: $RAG_DOCS/_TEXT_DEDUPE.csv.

Deliberately does NOT delete files. A duplicate is usually legitimate at the
source level - a bilingual OSHA PDF filed under both English and Espanol, or
an NFPA scan whose OCR text is also stored separately. Deleting one would
break the file -> source -> citation mapping.

Each text file gets a content hash and an `embed` flag with a reason. The
pack builder ships only embed=yes rows.

Exclusions, in order:
  bulk-data-spreadsheet  text from .xlsx - monitoring dumps, release tables,
                         exposure-model outputs. Thousands of rows of numbers;
                         28% of all text by volume and near-zero value as prose
                         chunks. Kept on disk for a future structured-data
                         path, never embedded.
  too-short              under 50 normalised chars
  duplicate              identical (after whitespace normalisation) to a file
                         that sorts earlier; `duplicate_of` names it

This pass is also the integrity check that nothing else is. It caught 205
"distinct" directive PDFs that were one file under 205 names - distinct
filenames, valid %PDF headers, plausible sizes, every other check passed.
Run it on every corpus.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config

SOURCE_EXTS = (".pdf", ".xlsx", ".docx", ".html", ".txt", ".doc")
NO_EMBED_FORMATS = {".xlsx"}
CHUNK_CHARS = 2000


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def build(root: Path | None = None, workers: int = 8) -> dict:
    root = root or config.rag_docs()
    text = root / "_TEXT"
    lock = threading.Lock()
    recs: list[dict] = []

    def source_format(p: Path) -> str:
        stem = root / p.relative_to(text).with_suffix("")
        for e in SOURCE_EXTS:
            if stem.with_suffix(e).exists():
                return e
        return ""

    def one(p: Path) -> None:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        body = t.split("-" * 72, 1)[-1] if "-" * 72 in t[:2000] else t
        n = _norm(body)
        h = "TOO_SHORT" if len(n) < 50 else hashlib.sha256(n.encode("utf-8")).hexdigest()
        with lock:
            recs.append({"text_path": p.relative_to(root).as_posix(),
                         "corpus": p.relative_to(text).parts[0],
                         "source_format": source_format(p),
                         "chars": len(body.strip()), "content_sha256": h})

    files = [Path(r) / f for r, _, fs in os.walk(text) for f in fs]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, files))

    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in recs:
        groups[r["content_sha256"]].append(r)
    for h, g in groups.items():
        if h == "TOO_SHORT":
            for r in g:
                r.update(embed="no", exclude_reason="too-short", duplicate_of="", dup_group_size=1)
            continue
        g.sort(key=lambda r: (len(r["text_path"]), r["text_path"]))
        for i, r in enumerate(g):
            r.update(embed="yes" if i == 0 else "no",
                     exclude_reason="" if i == 0 else "duplicate",
                     duplicate_of="" if i == 0 else g[0]["text_path"],
                     dup_group_size=len(g))
    for r in recs:                      # format exclusion wins over everything
        if r["source_format"] in NO_EMBED_FORMATS:
            r["embed"], r["exclude_reason"] = "no", "bulk-data-spreadsheet"

    recs.sort(key=lambda r: r["text_path"])
    cols = ["text_path", "corpus", "source_format", "chars", "content_sha256",
            "embed", "exclude_reason", "duplicate_of", "dup_group_size"]
    with open(root / "_TEXT_DEDUPE.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(recs)

    embed = [r for r in recs if r["embed"] == "yes"]
    chunks = sum(max(1, r["chars"] // CHUNK_CHARS) for r in embed)
    by = collections.defaultdict(lambda: {"files": 0, "embed": 0, "embed_chars": 0})
    for r in recs:
        by[r["corpus"]]["files"] += 1
        if r["embed"] == "yes":
            by[r["corpus"]]["embed"] += 1
            by[r["corpus"]]["embed_chars"] += r["chars"]
    return {"files": len(recs), "embed": len(embed),
            "embed_chars": sum(r["chars"] for r in embed),
            "excluded": dict(collections.Counter(r["exclude_reason"] for r in recs if r["embed"] == "no")),
            "chunks": chunks,
            "gb_int8": round(chunks * (1536 + 1500 + 2000 + 500) / 1e9, 2),
            "gb_f32": round(chunks * (1536 * 4 + 1500 + 2000 + 500) / 1e9, 2),
            "by_corpus": dict(by)}
