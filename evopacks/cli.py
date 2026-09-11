"""evopacks - the pipeline as subcommands, in pipeline order.

    evopacks collect  {osha-pubs|osha-interp|osha-dir|epa|nfpa-public|cfr-29|cfr-49|all}
    evopacks extract                 text layer for every document -> _TEXT/ + audit
    evopacks ocr                     tesseract over what extract flagged image-only
    evopacks index                   dedupe/exclusion index + MASTER-INDEX.csv
    evopacks build   [--only PACK]   dist/<pack>-<version>.tar.zst per pack-spec
    evopacks catalog [--release-base URL]   packs.json from dist/
    evopacks check   [PACK|--due] [--run-harvest]   source freshness, report only
    evopacks status                  what is on disk, what is stale

Set RAG_DOCS to the corpus root (default ./RAG-Docs). Every command is
idempotent and resumable.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import config


def _say(d: dict | list) -> None:
    print(json.dumps(d, indent=1, ensure_ascii=False, default=str))


def _record_capture(corpus: str) -> None:
    st = config.state_dir() / "captures.json"
    caps = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
    caps[corpus] = date.today().isoformat()
    st.write_text(json.dumps(caps, indent=1), encoding="utf-8")


def cmd_collect(a: argparse.Namespace) -> None:
    from .collect import ecfr, epa, nfpa_public, osha, osha_publications
    which = a.source
    runs = {
        "osha-pubs": (osha_publications.collect, "EHS"),
        "osha-interp": (osha.collect_interpretations, "OSHA-Interpretations"),
        "osha-dir": (osha.collect_directives, "OSHA-Directives"),
        "epa": (epa.collect, "EPA"),
        "nfpa-public": (nfpa_public.collect, "NFPA-Public"),
        "cfr-29": (lambda: ecfr.collect(29), "CFR-29"),
        "cfr-49": (lambda: ecfr.collect(49), "CFR-49"),
    }
    for name, (fn, corpus) in runs.items():
        if which not in ("all", name):
            continue
        print(f"== collect {name}", flush=True)
        _say(fn())
        _record_capture(corpus)


def cmd_extract(a: argparse.Namespace) -> None:
    from .extract.text import Extractor
    _say(Extractor(workers=a.workers).run())


def cmd_ocr(a: argparse.Namespace) -> None:
    from .extract.ocr import OCR, check_tools
    tools = check_tools()
    if "tesseract" not in tools or "pymupdf" not in tools:
        sys.exit(f"OCR needs tesseract + PyMuPDF; found: {tools}")
    _say({"tools": tools})
    _say(OCR(files_at_once=a.parallel, lang=a.lang).run())


def cmd_index(a: argparse.Namespace) -> None:
    from .index import dedupe, master
    _say({"dedupe": dedupe.build(), "master": master.build()})


def cmd_build(a: argparse.Namespace) -> None:
    from .build.pack import build_all
    _say(build_all(dist=Path(a.dist), only=a.only, version=a.version, captured=a.captured))


def cmd_catalog(a: argparse.Namespace) -> None:
    from .build.catalog import build
    _say(build(dist=Path(a.dist), out=Path(a.out), release_base=a.release_base))


def cmd_check(a: argparse.Namespace) -> None:
    from .check.freshness import check_pack, due
    packs = due() if a.due else ([a.pack] if a.pack else [])
    if not packs:
        _say({"due": []}); return
    _say([check_pack(p, run_harvest=a.run_harvest) for p in packs])


def cmd_status(a: argparse.Namespace) -> None:
    root = config.rag_docs()
    out: dict = {"rag_docs": str(root), "corpora": {}}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        n = sum(1 for _ in d.rglob("*") if _.is_file() and _.name.lower() not in
                ("manifest.csv", "readme.md", "readme.txt"))
        out["corpora"][d.name] = {"files": n, "manifest": (d / "manifest.csv").exists()}
    for f in ("_extraction_audit.csv", "_TEXT_DEDUPE.csv", "MASTER-INDEX.csv"):
        out[f] = (root / f).exists()
    st = config.state_dir() / "captures.json"
    out["captures"] = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
    from .check.freshness import due
    out["freshness_due"] = due()
    _say(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="evopacks", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="corpus root (overrides $RAG_DOCS)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect"); p.add_argument("source"); p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("extract"); p.add_argument("--workers", type=int, default=8); p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("ocr"); p.add_argument("--parallel", type=int, default=3)
    p.add_argument("--lang", default="eng"); p.set_defaults(fn=cmd_ocr)
    p = sub.add_parser("index"); p.set_defaults(fn=cmd_index)
    p = sub.add_parser("build"); p.add_argument("--dist", default="dist"); p.add_argument("--only")
    p.add_argument("--version"); p.add_argument("--captured"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("catalog"); p.add_argument("--dist", default="dist")
    p.add_argument("--out", default="packs.json"); p.add_argument("--release-base", default="")
    p.set_defaults(fn=cmd_catalog)
    p = sub.add_parser("check"); p.add_argument("pack", nargs="?"); p.add_argument("--due", action="store_true")
    p.add_argument("--run-harvest", action="store_true"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)

    a = ap.parse_args(argv)
    if a.root:
        import os
        os.environ["RAG_DOCS"] = a.root
    a.fn(a)


if __name__ == "__main__":
    main()
