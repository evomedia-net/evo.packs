"""The OSHA packs ship the vocabulary their corpus is written in (#12).

"How often must HAZWOPER workers be retrained?" - an example on evo.ehs's
Ask AI docs page - was refused with the OSHA pack ticked. 29 CFR never says
"HAZWOPER" and says "refresher training" where people say "retrained", so
nothing retrieved the rule. Asked as "how often must hazardous waste
operations workers receive refresher training?", the same pack on the same
stack answered it: eight hours annually, 1910.120(e)(8).

dot-hazmat fixed the same gap with a map in its spec (evo.ai#209). This is
the OSHA one. Every target term was counted in the cfr-29 text before it went
in - a map that points at words the corpus does not use only adds noise.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "pack-specs" / "osha.json").read_text(encoding="utf-8"))
MAP = SPEC.get("query_expansion", {})

MUST_EXPAND = {
    "How often must HAZWOPER workers be retrained?":
        ["hazardous waste operations and emergency response", "refresher training"],
    "What does our LOTO procedure have to cover?": ["control of hazardous energy"],
    "Do our forklift drivers need certification?": ["powered industrial truck"],
    "When do we need a respirator fit test?": ["respiratory protection"],
}
MUST_NOT_EXPAND = [
    "How do I remove a permit?",
    "Which permits are due next?",
    "What is the permissible exposure limit for benzene?",
    "Who is upsmart's contact?",
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


def test_the_osha_spec_ships_a_map():
    assert MAP, "the pack that needed this ships no vocabulary"


def test_every_value_is_a_list_of_strings_and_every_key_lower_case():
    """evo-ai refuses a malformed map and retrieves without it, so a mistake
    here is silent there."""
    for phrase, terms in MAP.items():
        assert phrase == phrase.lower(), f"{phrase!r} is matched case-insensitively"
        assert isinstance(terms, list) and terms, phrase
        assert all(isinstance(t, str) and t.strip() for t in terms), phrase


@pytest.mark.parametrize("question,expected", MUST_EXPAND.items())
def test_the_lay_question_gains_the_regulation_vocabulary(question, expected):
    added = _fires(question)
    for term in expected:
        assert term in added, f"{question!r} did not gain {term!r}: {added}"


@pytest.mark.parametrize("question", MUST_NOT_EXPAND)
def test_questions_already_in_the_corpus_register_are_left_alone(question):
    """Word boundaries, not substrings: "psm" must not fire inside
    "upsmart", and a question already in regulation words needs nothing."""
    assert _fires(question) == [], f"{question!r} was expanded"


def test_every_member_is_built_with_the_meta_spec():
    """The map lives on the meta spec; the members are the artifacts evo-ai
    installs. They get it because each member is built from a copy of the
    meta spec - if that stopped, the map would never leave this repo."""
    src = (ROOT / "evopacks" / "build" / "pack.py").read_text(encoding="utf-8")
    assert 'sub = {**spec, "title": MEMBER_TITLES.get(mid, human_name(mid))}' in src
    assert '"query_expansion": spec.get("query_expansion", {})' in src
