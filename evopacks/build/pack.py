"""Build pack artifacts from the corpus.

A pack ships TEXT, not vectors, and only the rows index/dedupe.py marked
embed=yes. Every file entry carries the source URL and sha256 from the corpus
manifest, so a citation inside evo.ai can resolve back to the publisher.

Content pack  -> dist/<pack_id>-<version>.tar.zst containing
                   manifest.json  (pack metadata + one entry per file)
                   text/<...>.txt (the embeddable text, corpus-relative paths)
Meta pack     -> dist/<pack_id>-<version>.tar.zst containing only manifest.json
                 (members + recommends); members are their own artifacts.

`epa` expands one member per EPA topic section present on disk, per
pack-specs/epa.json `members_from`.

Versions are date-stamped from the corpus capture date (YYYY.MM.DD) unless
overridden - a pack version names what SHIPPED, so two builds from one capture
get one version.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
from datetime import date
from pathlib import Path

import zstandard

from .. import config

csv.field_size_limit(10_000_000)
SPEC_DIR = Path(__file__).resolve().parents[2] / "pack-specs"


def load_specs() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SPEC_DIR.glob("*.json"))}


def _manifest_rows(root: Path, corpus: str) -> dict[str, dict]:
    f = root / corpus / "manifest.csv"
    if not f.exists():
        return {}
    with open(f, encoding="utf-8-sig") as fh:
        return {r["relative_path"]: r for r in csv.DictReader(fh)}


def _embed_rows(root: Path, corpus: str) -> list[dict]:
    with open(root / "_TEXT_DEDUPE.csv", encoding="utf-8-sig") as fh:
        return [r for r in csv.DictReader(fh) if r["embed"] == "yes" and r["corpus"] == corpus]


def captured_at(root: Path, corpus: str, override: str | None) -> str:
    if override:
        return override
    st = config.state_dir() / "captures.json"
    if st.exists():
        v = json.loads(st.read_text(encoding="utf-8")).get(corpus)
        if v:
            return v
    return date.today().isoformat()


def _files_for(root: Path, corpus: str, section: str | None) -> list[dict]:
    """Embed rows for a corpus (or one EPA section), joined to manifest provenance."""
    man = _manifest_rows(root, corpus)
    out = []
    for r in _embed_rows(root, corpus):
        text_rel = r["text_path"]                                  # _TEXT/<corpus>/...
        inner = text_rel.split("/", 2)[2]                          # <...>.txt
        if section is not None and not inner.startswith(section + "/"):
            continue
        # manifests store forward-slash paths; Path.__str__ gives backslashes on
        # Windows and the join silently misses (caught by the artifact check:
        # 1,049 entries with empty source_url)
        src_rel = Path(inner).with_suffix(r["source_format"] or ".txt").as_posix()
        m = man.get(src_rel, {})
        out.append({"path": f"text/{inner}", "source_path": f"{corpus}/{src_rel}",
                    "source_url": m.get("source_url", ""), "source_sha256": m.get("sha256", ""),
                    "title": m.get("title") or m.get("subject") or "",
                    "identifier": (m.get("publication_id") or m.get("citation")
                                   or m.get("directive_number") or m.get("standards_cited")
                                   or m.get("section") or m.get("category") or ""),
                    "language": m.get("language", "English"),
                    "chars": int(r["chars"]), "text_file": root / text_rel})
    return out


def _write_tar(dest: Path, manifest: dict, files: list[dict]) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor(level=10, threads=-1)
    with open(dest, "wb") as raw, cctx.stream_writer(raw) as z, tarfile.open(fileobj=z, mode="w|") as tar:
        data = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8")
        ti = tarfile.TarInfo("manifest.json"); ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
        for f in files:
            tar.add(str(f["text_file"]), arcname=f["path"])
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix(dest.suffix + ".sha256").write_text(f"{sha}  {dest.name}\n")
    return sha


def build_content(pack_id: str, spec: dict, root: Path, dist: Path, version: str,
                  captured: str, corpus: str, section: str | None = None,
                  title: str | None = None) -> dict:
    files = _files_for(root, corpus, section)
    if not files:
        return {"pack_id": pack_id, "skipped": "no embed=yes text"}
    manifest = {
        "pack_id": pack_id, "kind": "content", "version": version,
        "title": title or spec.get("title", pack_id), "publisher": spec["publisher"],
        "rights_tier": spec["rights_tier"], "rights_note": spec.get("rights_note", ""),
        "corpus": corpus, "section": section, "captured_at": captured,
        "refresh": spec.get("refresh", {}),
        "doc_count": len(files), "text_bytes": sum(f["chars"] for f in files),
        "files": [{k: v for k, v in f.items() if k != "text_file"} for f in files],
    }
    dest = dist / f"{pack_id}-{version}.tar.zst"
    sha = _write_tar(dest, manifest, files)
    return {"pack_id": pack_id, "artifact": dest.name, "sha256": sha,
            "doc_count": len(files), "text_bytes": manifest["text_bytes"],
            "bytes": dest.stat().st_size}


def build_meta(pack_id: str, spec: dict, members: list[str], dist: Path, version: str,
               captured: str) -> dict:
    manifest = {"pack_id": pack_id, "kind": "meta", "version": version,
                "title": spec["title"], "publisher": spec["publisher"],
                "rights_tier": spec["rights_tier"], "rights_note": spec.get("rights_note", ""),
                "captured_at": captured, "members": members,
                "required_members": spec.get("required_members", []),
                "recommends": spec.get("recommends", []), "refresh": spec.get("refresh", {}),
                "description": spec.get("description", "")}
    dest = dist / f"{pack_id}-{version}.tar.zst"
    sha = _write_tar(dest, manifest, [])
    return {"pack_id": pack_id, "artifact": dest.name, "sha256": sha, "members": members,
            "bytes": dest.stat().st_size}


CORPUS_OF = {"cfr-29": "CFR-29", "osha-interpretations": "OSHA-Interpretations",
             "osha-directives": "OSHA-Directives", "osha-publications": "EHS"}


def build_all(root: Path | None = None, dist: Path | None = None, only: str | None = None,
              version: str | None = None, captured: str | None = None) -> list[dict]:
    root = root or config.rag_docs()
    dist = dist or Path("dist")
    specs = load_specs()
    results = []
    for pid, spec in specs.items():
        if only and pid != only:
            continue
        if spec["kind"] == "content":
            corpus = spec["corpus"]
            cap = captured_at(root, corpus, captured)
            ver = version or cap.replace("-", ".")
            results.append(build_content(pid, spec, root, dist, ver, cap, corpus))
            continue
        # meta pack
        members: list[str] = []
        if "members_from" in spec:
            mf = spec["members_from"]
            cap = captured_at(root, mf["corpus"], captured)
            ver = version or cap.replace("-", ".")
            sections = sorted({r["text_path"].split("/")[2]
                               for r in _embed_rows(root, mf["corpus"])})
            for s in sections:
                if s in mf.get("exclude_sections", []):
                    continue
                mid = mf["member_id"].format(section=s)
                results.append(build_content(mid, spec, root, dist, ver, cap, mf["corpus"], s,
                                             title=f"{spec['title']} - {s}"))
                members.append(mid)
        else:
            # each member is versioned from ITS OWN capture; the meta-pack takes
            # the newest, since a bundle is only as current as its latest member
            caps = []
            for mid in spec["members"]:
                corpus = CORPUS_OF[mid]
                mcap = captured_at(root, corpus, captured)
                caps.append(mcap)
                sub = {**spec, "title": mid}
                results.append(build_content(mid, sub, root, dist,
                                             version or mcap.replace("-", "."), mcap, corpus))
                members.append(mid)
            cap = max(caps)
            ver = version or cap.replace("-", ".")
        results.append(build_meta(pid, spec, members, dist, ver, cap))
    (dist / "build-results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    return results
