"""packs.json - what the installer reads before pulling an artifact.

Generated from the manifests inside dist/*.tar.zst, so the catalog can never
describe an artifact that does not exist. Fields the installer needs up front:
size (to check disk before install), rights tier (to print), sha256 (to
verify), captured_at (to show age), members/recommends (for meta-packs).
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import zstandard


def read_manifest(artifact: Path) -> dict:
    dctx = zstandard.ZstdDecompressor()
    with open(artifact, "rb") as raw, dctx.stream_reader(raw) as z:
        buf = io.BytesIO(z.read())
    with tarfile.open(fileobj=buf, mode="r:") as tar:
        m = tar.extractfile("manifest.json")
        return json.loads(m.read().decode("utf-8")) if m else {}


def build(dist: Path | None = None, out: Path | None = None,
          release_base: str = "") -> dict:
    dist = dist or Path("dist")
    out = out or Path("packs.json")
    packs = []
    for art in sorted(dist.glob("*.tar.zst")):
        m = read_manifest(art)
        if not m:
            continue
        entry = {
            "pack_id": m["pack_id"], "kind": m["kind"], "version": m["version"],
            "title": m["title"], "publisher": m["publisher"],
            "rights_tier": m["rights_tier"], "rights_note": m.get("rights_note", ""),
            "captured_at": m["captured_at"], "refresh": m.get("refresh", {}),
            "artifact": art.name, "artifact_bytes": art.stat().st_size,
            "artifact_sha256": hashlib.sha256(art.read_bytes()).hexdigest(),
            "url": f"{release_base.rstrip('/')}/{art.name}" if release_base else "",
        }
        if m["kind"] == "content":
            entry.update(doc_count=m["doc_count"], text_bytes=m["text_bytes"],
                         corpus=m.get("corpus", ""), section=m.get("section"))
        else:
            entry.update(members=m["members"], required_members=m.get("required_members", []),
                         recommends=m.get("recommends", []), description=m.get("description", ""))
        packs.append(entry)
    catalog = {"schema": 1, "packs": packs}
    out.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"packs": len(packs), "content": sum(1 for p in packs if p["kind"] == "content"),
            "meta": sum(1 for p in packs if p["kind"] == "meta"), "catalog": str(out)}
