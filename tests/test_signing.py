"""packs.json is signed, and a sealed deployment can check it (evo.ai#242).

A pack folder carried to a deployment with no network holds the catalog and
the artifacts together, so the checksums in the catalog prove the artifacts
match it - and nothing more. The signature is what proves the catalog came
from this pipeline.

The known-answer test at the bottom pins the FORMAT. evo-ai's verifier
carries the same vector, so a change on either side that would make a real
release unverifiable fails a test in both repos, not an install on a box
nobody can reach.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evopacks.build import sign


@pytest.fixture
def key(tmp_path):
    return sign.keygen(tmp_path / "keys" / "evopacks-signing.pem")


@pytest.fixture
def folder(tmp_path, key):
    """A release as it would be carried across: catalog, signature, artifacts."""
    d = tmp_path / "release"
    d.mkdir()
    arts = {"cfr-29": b"cfr-29 artifact bytes", "dot-hazmat": b"dot-hazmat artifact bytes"}
    for pid, data in arts.items():
        (d / f"{pid}.tar.zst").write_bytes(data)
    catalog = {"schema": 1, "packs": [
        {"pack_id": pid, "artifact": f"{pid}.tar.zst",
         "artifact_sha256": hashlib.sha256(data).hexdigest()} for pid, data in arts.items()]}
    (d / "packs.json").write_text(json.dumps(catalog, indent=1) + "\n", encoding="utf-8")
    sign.sign(d / "packs.json", Path(key["private_key"]))
    return d


# ── sign and verify ──────────────────────────────────────────────────────

def test_a_signed_folder_verifies(folder, key):
    out = sign.verify_folder(folder, key["public_key"])
    assert out == {"signature": "valid", "verified": ["cfr-29", "dot-hazmat"],
                   "mismatched": [], "absent": []}


def test_a_catalog_changed_after_signing_fails(folder, key):
    """The case the signature exists for: someone edits a checksum to match a
    swapped artifact. The checksum check alone would pass it."""
    p = folder / "packs.json"
    p.write_text(p.read_text(encoding="utf-8").replace("cfr-29", "cfr-30"), encoding="utf-8")
    with pytest.raises(sign.SignatureError, match="changed after it was signed"):
        sign.verify_folder(folder, key["public_key"])


def test_a_different_key_fails(folder, tmp_path):
    other = sign.keygen(tmp_path / "other" / "k.pem")
    with pytest.raises(sign.SignatureError, match="signed by a different key"):
        sign.verify_folder(folder, other["public_key"])


def test_no_signature_fails(folder, key):
    (folder / "packs.json.sig").unlink()
    with pytest.raises(sign.SignatureError, match="not signed"):
        sign.verify_folder(folder, key["public_key"])


def test_a_garbled_public_key_says_so(folder):
    with pytest.raises(sign.SignatureError, match="not a base64 Ed25519 public key"):
        sign.verify_folder(folder, "not-a-key")


def test_a_swapped_artifact_is_reported(folder, key):
    (folder / "dot-hazmat.tar.zst").write_bytes(b"something else")
    out = sign.verify_folder(folder, key["public_key"])
    assert out["mismatched"] == ["dot-hazmat"] and out["verified"] == ["cfr-29"]


def test_a_subset_folder_reports_what_is_absent(folder, key):
    """Carrying only the packs a site wants is fine; it is reported, not failed."""
    (folder / "cfr-29.tar.zst").unlink()
    out = sign.verify_folder(folder, key["public_key"])
    assert out["absent"] == ["cfr-29"] and out["verified"] == ["dot-hazmat"]


# ── the key is hard to lose and hard to leak ─────────────────────────────

def test_keygen_will_not_overwrite_a_key(key):
    with pytest.raises(sign.SignatureError, match="orphan every deployment"):
        sign.keygen(Path(key["private_key"]))


def test_keygen_refuses_a_path_inside_a_repository(tmp_path):
    repo = tmp_path / "some-repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(sign.SignatureError, match="inside the git working tree"):
        sign.keygen(repo / "keys" / "k.pem")
    assert not (repo / "keys" / "k.pem").exists()


# ── the CLI exits non-zero on anything an operator must not carry in ─────

def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "evopacks.cli", *args],
                          capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parents[1])


def test_cli_verify_passes_a_good_folder(folder, key):
    r = _cli("verify", str(folder), "--public-key", key["public_key"])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["signature"] == "valid"


def test_cli_verify_fails_a_swapped_artifact(folder, key):
    (folder / "cfr-29.tar.zst").write_bytes(b"swapped")
    r = _cli("verify", str(folder), "--public-key", key["public_key"])
    assert r.returncode != 0 and "do not match packs.json" in r.stderr


def test_cli_verify_fails_a_bad_signature(folder, tmp_path):
    other = sign.keygen(tmp_path / "o" / "k.pem")
    r = _cli("verify", str(folder), "--public-key", other["public_key"])
    assert r.returncode != 0 and "does not match its signature" in r.stderr


# ── the format, pinned: evo-ai's tests carry the same vector ─────────────

_SEED = bytes(range(32))
_CATALOG = b'{"schema": 1, "packs": []}\n'
_PUBLIC = "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="
_SIGNATURE = ("Z78fVD2Z/ocWGFLj/ivI39EQEH5Pd94xXEXKiGQ0ojZ7Eoy/BAOnMCf372Ppkm822J1wnB/"
              "L2/kX2QKhygsTBA==")


def test_known_answer_signature():
    private = Ed25519PrivateKey.from_private_bytes(_SEED)
    assert sign.public_key_b64(private) == _PUBLIC
    assert sign.sign_bytes(private, _CATALOG) == _SIGNATURE
    sign.verify_bytes(_CATALOG, _SIGNATURE, _PUBLIC)
