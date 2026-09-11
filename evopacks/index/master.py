"""Build $RAG_DOCS/MASTER-INDEX.csv: every file in every corpus, one row each.

Each corpus manifest has its own columns (publication ids, standards cited,
edition years, EPA sections) so the mapping onto the common index is
per-corpus and explicit. `rights` travels with every row on purpose: the
corpora are not uniform - five are public domain, one is NFPA copyright
offered free, one (nfpa-ibr) is NFPA copyright and contested. They must never
be merged without that column.

Also reconciles the index against disk: missing and unlisted counts are
returned and should both be zero.
"""
from __future__ import annotations

import collections
import csv
import os
from pathlib import Path

from .. import config

COLS = ["corpus", "relative_path", "language", "format", "title", "identifier",
        "source_url", "source_page", "publisher", "rights", "bytes", "sha256"]

OSHA = "U.S. Occupational Safety and Health Administration"
GPO = "U.S. Government Publishing Office / eCFR"
PD = "Public domain (17 U.S.C. 105) - U.S. federal government work"
IBR = ("Copyright NFPA; posted by Public.Resource.Org as incorporated by reference "
       "into law - not distributed as a pack; see NFPA/README.md")
FREE = ("Copyright NFPA; offered free by NFPA for public education/research - "
        "see NFPA-Public/README.md")
META = {"manifest.csv", "readme.md", "readme.txt", "sources.md", "sources.txt",
        "master-index.csv", "_extraction_audit.csv", "_text_dedupe.csv"}

csv.field_size_limit(10_000_000)


def _rd(root: Path, *p: str) -> list[dict]:
    f = root.joinpath(*p)
    if not f.exists():
        return []
    with open(f, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def build(root: Path | None = None) -> dict:
    root = root or config.rag_docs()
    out: list[dict] = []

    def add(**kw):
        out.append(kw)

    for r in _rd(root, "EHS", "manifest.csv"):
        add(corpus="OSHA-Publications", relative_path="EHS/" + r["relative_path"],
            language=r["language"], format=r["format"], title=r["title"],
            identifier=r["publication_id"], source_url=r["source_url"],
            source_page="https://www.osha.gov/publications", publisher=OSHA, rights=PD,
            bytes=r["bytes"], sha256=r["sha256"])
    for r in _rd(root, "OSHA-Interpretations", "manifest.csv"):
        add(corpus="OSHA-Interpretations",
            relative_path="OSHA-Interpretations/" + r["relative_path"],
            language="English", format="TEXT", title=r["title"],
            identifier=(r["standards_cited"] or r["identifier"])[:150],
            source_url=r["source_url"], source_page=r["source_url"], publisher=OSHA,
            rights=PD, bytes=r["bytes"], sha256=r["sha256"])
    for r in _rd(root, "OSHA-Directives", "manifest.csv"):
        add(corpus="OSHA-Directives", relative_path="OSHA-Directives/" + r["relative_path"],
            language="English", format=r["format"], title=r["title"],
            identifier=r["directive_number"] or r["identifier"],
            source_url=r["source_url"], source_page=r["source_url"], publisher=OSHA,
            rights=PD, bytes=r["bytes"], sha256=r["sha256"])
    for corpus in ("CFR-29", "CFR-49"):
        for r in _rd(root, corpus, "manifest.csv"):
            add(corpus=corpus, relative_path=f"{corpus}/" + r["relative_path"],
                language="English", format="TEXT", title=r["subject"],
                identifier=r["citation"], source_url=r["source_url"],
                source_page=r["source_url"], publisher=GPO, rights=PD,
                bytes=r["bytes"], sha256=r["sha256"])
    for r in _rd(root, "EPA", "manifest.csv"):
        add(corpus="EPA", relative_path="EPA/" + r["relative_path"],
            language="English", format=r["format"], title=r["title"],
            identifier=r["section"], source_url=r["source_url"],
            source_page=r["source_page"], publisher="U.S. Environmental Protection Agency",
            rights=PD, bytes=r["bytes"], sha256=r["sha256"])
    for r in _rd(root, "NFPA", "manifest.csv"):
        add(corpus="NFPA-IBR", relative_path="NFPA/" + r["relative_path"],
            language="English", format=r["kind"], title=r["title"],
            identifier=f'{r["standard"]} ({r["edition_year"]})'.strip(),
            source_url=r["source_url"], source_page=r["source_details_page"],
            publisher=r["publisher"], rights=IBR, bytes=r["bytes"], sha256=r["sha256"])
    for r in _rd(root, "NFPA-Public", "manifest.csv"):
        add(corpus="NFPA-Public", relative_path="NFPA-Public/" + r["relative_path"],
            language=r["language"], format=r["format"], title=r["title"],
            identifier=f'{r["collection"]}/{r["category"]}'.strip("/"),
            source_url=r["source_url"], source_page=r["source_page"],
            publisher=r["publisher"], rights=FREE, bytes=r["bytes"], sha256=r["sha256"])

    with open(root / "MASTER-INDEX.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(out)

    disk = set()
    for r, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d != "_TEXT"]
        for fn in fs:
            if fn.lower() in META or fn.endswith(".part"):
                continue
            disk.add(str(Path(r, fn)).lower())
    listed = {str(root / r["relative_path"]).lower() for r in out}
    by = collections.Counter(r["corpus"] for r in out)
    return {"rows": len(out), "bytes": sum(int(r["bytes"]) for r in out),
            "by_corpus": dict(by), "disk": len(disk),
            "missing": len(listed - disk), "unlisted": len(disk - listed),
            "all_have_url": all(r["source_url"].startswith("http") for r in out),
            "all_have_sha": all(len(r["sha256"]) == 64 for r in out)}
