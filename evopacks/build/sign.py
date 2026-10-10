"""Sign packs.json, so a deployment can tell a release from a copy (evo.ai#242).

A sealed deployment installs packs from a folder carried in on removable
media. The sha256 in packs.json proves each artifact matches the catalog
beside it - but nothing proved the catalog itself came from this pipeline,
because the catalog and the artifacts travel on the same stick. Over the
network, HTTPS and GitHub vouched for that; offline, nothing did.

An Ed25519 signature over packs.json closes the gap. evo-ai pins the public
key (EVOPACKS_PUBLIC_KEY), so trust runs signature -> catalog -> each
artifact's sha256 -> the manifest inside.

The format is kept small enough that the verifier needs nothing beyond
`cryptography`, which evo-ai already depends on:

* ``packs.json.sig`` - base64 of the 64-byte raw Ed25519 signature over the
  EXACT bytes of packs.json, on one line. Re-generating packs.json after
  signing makes the signature fail, which is the point.
* the public key - base64 of its 32 raw bytes. That string is what a
  deployment sets EVOPACKS_PUBLIC_KEY to.
* the private key - a PKCS#8 PEM file that lives OUTSIDE any repository and
  is backed up out of band. Losing it means minting a new key and re-pinning
  every deployment; leaking it means anyone can sign a catalog.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

CATALOG = "packs.json"
SIGNATURE = CATALOG + ".sig"


class SignatureError(Exception):
    """packs.json is unsigned, or does not match its signature."""


def _inside_a_repo(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def public_key_b64(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def keygen(out: Path) -> dict:
    """Mint a signing key at `out` and return the public half.

    Refuses to overwrite a key (that would orphan every deployment pinned to
    it) and refuses a path inside a git working tree, where one `git add -A`
    would publish it.
    """
    out = out.resolve()
    if out.exists():
        raise SignatureError(f"{out} already exists; a new key would orphan every "
                             f"deployment pinned to the old one")
    repo = _inside_a_repo(out.parent)
    if repo is not None:
        raise SignatureError(f"{out} is inside the git working tree {repo}; keep the "
                             f"signing key outside every repository")
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pem)
    return {"private_key": str(out), "public_key": public_key_b64(private)}


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SignatureError(f"{path} is not an Ed25519 private key")
    return key


def sign_bytes(private: Ed25519PrivateKey, catalog: bytes) -> str:
    return base64.b64encode(private.sign(catalog)).decode("ascii")


def sign(catalog: Path, key: Path) -> dict:
    """Write <catalog>.sig beside the catalog."""
    private = load_private(key)
    sig_path = catalog.with_name(catalog.name + ".sig")
    sig_path.write_text(sign_bytes(private, catalog.read_bytes()) + "\n", encoding="ascii")
    return {"catalog": str(catalog), "signature": str(sig_path),
            "public_key": public_key_b64(private)}


def verify_bytes(catalog: bytes, signature: str | None, public_key: str) -> None:
    """Raise SignatureError unless `signature` is `public_key`'s over `catalog`.
    The same check evo-ai makes before it reads a catalog."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key.strip(), validate=True))
    except (ValueError, binascii.Error) as e:
        raise SignatureError("the public key is not a base64 Ed25519 public key") from e
    if signature is None:
        raise SignatureError(f"{CATALOG} is not signed: there is no {SIGNATURE} beside it")
    try:
        sig = base64.b64decode(signature.strip(), validate=True)
    except (ValueError, binascii.Error) as e:
        raise SignatureError(f"{SIGNATURE} is not base64") from e
    try:
        pub.verify(sig, catalog)
    except InvalidSignature as e:
        raise SignatureError(
            f"{CATALOG} does not match its signature: it was changed after it was "
            f"signed, or signed by a different key") from e


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_folder(folder: Path, public_key: str) -> dict:
    """Check a pack folder before it is carried across: the signature, then
    every artifact that is present against its sha256.

    A folder may hold a subset of the catalog - a pack whose artifact is not
    there is reported as absent, not as a failure, because evo-ai refuses to
    install it with the same message.
    """
    sig_path = folder / SIGNATURE
    verify_bytes((folder / CATALOG).read_bytes(),
                 sig_path.read_text(encoding="ascii") if sig_path.exists() else None,
                 public_key)
    catalog = json.loads((folder / CATALOG).read_text(encoding="utf-8-sig"))
    out: dict = {"signature": "valid", "verified": [], "mismatched": [], "absent": []}
    for p in catalog.get("packs", []):
        art = folder / p.get("artifact", "")
        if not p.get("artifact") or not art.is_file():
            out["absent"].append(p["pack_id"])
        elif _sha256(art) == (p.get("artifact_sha256") or "").lower():
            out["verified"].append(p["pack_id"])
        else:
            out["mismatched"].append(p["pack_id"])
    return out
