"""A pack ships the vocabulary its own corpus is written in (evo.ai#209).

Measured against the built dot-hazmat vectors: "How I transfer
nitroglycerin?" did not retrieve nitroglycerin at all. Four of five chunks
were nitric acid, nitric oxide or nitrocellulose - names that embed near it
- and the model refused, correctly, on a context about a different
substance.

49 CFR does not say "transfer between plants". It says "carriage by public
highway", "loading and unloading", "Class 1 (explosive) materials". With
those terms appended to the reader's own words, the same collection returns
177.835(j), titled "Transfer of Class 1 (explosive) materials en route".

The map belongs here rather than in evo-ai, for the same reason the corpus
does: it describes 49 CFR. A deployment cannot write it without reading the
regulations, and evo-ai must not carry pack data.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "pack-specs"
DOT = json.loads((SPEC_DIR / "dot-hazmat.json").read_text(encoding="utf-8"))
MAP = DOT.get("query_expansion", {})

#: The failing questions, and the ones that must not change.
MUST_EXPAND = [
    "How I transfer nitroglycerin?",
    "How do I move nitroglycerin between plants?",
    "How do I ship dynamite?",
]
MUST_NOT_EXPAND = [
    "What is the hazard class for UN3319?",
    "What placard is required for a Division 1.1 explosive?",
    "How do I remove a permit?",
]


def _fires(question: str) -> list[str]:
    """The terms this map would append, with evo-ai's matching rules:
    case-insensitive, on word boundaries, already-present terms skipped."""
    lowered = question.lower()
    added: list[str] = []
    for phrase, terms in sorted(MAP.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(phrase.lower())}\b", lowered):
            for t in terms:
                if t.lower() not in lowered and t.lower() not in (a.lower() for a in added):
                    added.append(t)
    return added


# ── the map exists and is shaped the way evo-ai expects ──────────────────

def test_dot_hazmat_ships_a_map():
    assert MAP, "the pack that needed this ships no vocabulary"


def test_every_value_is_a_list_of_strings():
    """evo-ai refuses a malformed map and retrieves without it, so a mistake
    here is silent there."""
    for phrase, terms in MAP.items():
        assert isinstance(terms, list), f"{phrase!r} maps to {type(terms).__name__}"
        assert terms, f"{phrase!r} maps to an empty list"
        assert all(isinstance(t, str) and t.strip() for t in terms), phrase


def test_the_keys_are_lay_terms_not_regulation_terms():
    """The map translates INTO the corpus's register. A key already written
    in regulation language would never be what a reader typed."""
    for phrase in MAP:
        assert "carriage" not in phrase.lower()
        assert phrase.lower() == phrase, f"{phrase!r} is matched case-insensitively"


# ── what it does to the questions that mattered ──────────────────────────

@pytest.mark.parametrize("q", MUST_EXPAND)
def test_the_failing_questions_gain_the_regulation_vocabulary(q):
    added = _fires(q)
    assert added, f"nothing added to {q!r}"
    assert any("carriage by public highway" in t or "transporting" in t
               or "offered for transportation" in t for t in added), added


@pytest.mark.parametrize("q", MUST_NOT_EXPAND)
def test_the_lookup_questions_are_left_alone(q):
    """Appending transport vocabulary to "hazard class for UN3319" pushed the
    172.101 table row out of the results, and that row IS the answer."""
    assert _fires(q) == [], f"{q!r} was expanded"


def test_move_does_not_fire_on_remove():
    """Word boundaries, not substrings - otherwise a question about removing
    a permit gets explosives vocabulary."""
    assert _fires("How do I remove a permit?") == []


def test_a_transport_verb_reaches_the_carriage_sections():
    added = _fires("How I transfer nitroglycerin?")
    assert "loading and unloading" in added
    assert "Class 1 explosive materials" in added


# ── it reaches the artifact ──────────────────────────────────────────────

def test_the_builder_copies_it_into_the_manifest():
    """evo-ai reads it from the manifest at install. A map that stays in the
    spec never leaves this repo."""
    src = (ROOT / "evopacks" / "build" / "pack.py").read_text(encoding="utf-8")
    assert '"query_expansion": spec.get("query_expansion", {})' in src


def test_a_pack_without_a_map_still_builds():
    """Every other pack in the catalogue ships none, and must be unaffected."""
    for spec_file in SPEC_DIR.glob("*.json"):
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        assert isinstance(spec.get("query_expansion", {}), dict), spec_file.name
