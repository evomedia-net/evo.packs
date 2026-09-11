"""evopacks - build, catalog, and freshness-check content packs for evo.ai.

Pipeline, in order:

  collect  -> fetch a source corpus into $RAG_DOCS/<corpus>/ with a manifest
  extract  -> text layer for every document into $RAG_DOCS/_TEXT/, audited
  ocr      -> tesseract over documents the audit found image-only
  index    -> dedupe/exclusion index and the cross-corpus MASTER-INDEX
  build    -> one tar.zst + manifest per pack, from the embed=yes rows
  check    -> per-pack source freshness, report only

Every stage is idempotent and resumable: re-running skips what is already on
disk, so a rate-limit block or a crash costs the remaining work, not the done
work.
"""

__version__ = "0.0.0.1.0"
