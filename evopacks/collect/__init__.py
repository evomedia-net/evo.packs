"""Collectors: one module per source, each writing $RAG_DOCS/<corpus>/ + manifest.csv.

Every collector is resumable (skips files already on disk) and records the
exact URL each file came from, so provenance survives into the pack.
"""
