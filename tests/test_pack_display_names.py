"""A pack's title is a name, not its slug or its id.

The title is what evo-ai stores in its `packs` row at install time, and what
the Global Admin card and every workspace's Reference Packs list render. What
shipped read:

    EPA - EHS guidance by topic - assessing-and-managing-chemicals-under-tsca
    EPA - EHS guidance by topic - hw-sw846
    osha-interpretations
    cfr-29

Two bugs in one function: an EPA member took the parent title plus the raw
URL slug, and a meta pack's members took `title = mid`, so four packs had no
name at all beyond their identifier.

The hard part is acronyms - plain title case gives "Tsca", "Pcbs", "Aegl" -
and EPA's own run-together slugs ("greenchemistry", "indoorairplus"), which
no rule derives. Those are an explicit list; everything else goes through the
general rule, and these tests pin both halves.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from evopacks.build.pack import MEMBER_TITLES, SECTION_TITLES, human_name

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("slug, expected", [
    ("asbestos", "Asbestos"),
    ("coal-combustion-residuals", "Coal Combustion Residuals"),
    # acronyms survive title case
    ("tsca", "TSCA"),
    ("pcbs", "PCBs"),
    ("rmp", "RMP"),
    ("pfas", "PFAS"),
    # small words stay small unless they lead
    ("assessing-and-managing-chemicals-under-tsca",
     "Assessing and Managing Chemicals under TSCA"),
    # the explicit overrides
    ("greenchemistry", "Green Chemistry"),
    ("indoorairplus", "Indoor airPLUS"),
    ("indoor-air-quality-iaq", "Indoor Air Quality"),
])
def test_a_slug_becomes_a_name(slug, expected):
    assert human_name(slug) == expected


def test_no_generated_name_still_looks_like_a_slug():
    """The whole published catalogue, run through the namer."""
    catalog = json.loads((ROOT / "packs.json").read_text(encoding="utf-8"))
    for pack in catalog["packs"]:
        pid = pack["pack_id"]
        if not pid.startswith("epa-"):
            continue
        name = human_name(pid[len("epa-"):])
        assert "-" not in name.replace(" - ", "").replace("SW-846", ""), name
        assert name[0].isupper() or name[0].isdigit(), name
        # "Tsca" / "Pcbs": an acronym that got title-cased into a word
        assert not re.search(r"\b(Tsca|Pcbs|Rcra|Aegl|Rmp|Pfas|Ust|Epcra|Tri)\b", name), name


def test_every_meta_member_has_a_written_name():
    """These are whole corpora, not topic slugs, so nothing is derived."""
    for pid, title in MEMBER_TITLES.items():
        assert title != pid, pid
        assert not title.islower(), pid
        assert len(title) > len(pid), pid


def test_the_overrides_are_all_still_reachable():
    """An override for a section the catalogue no longer publishes is dead
    weight that reads as coverage."""
    catalog = json.loads((ROOT / "packs.json").read_text(encoding="utf-8"))
    sections = {p["pack_id"][len("epa-"):] for p in catalog["packs"]
                if p["pack_id"].startswith("epa-")}
    unreachable = set(SECTION_TITLES) - sections
    assert not unreachable, f"overrides match no published section: {unreachable}"


def test_member_titles_name_packs_that_exist():
    catalog = json.loads((ROOT / "packs.json").read_text(encoding="utf-8"))
    ids = {p["pack_id"] for p in catalog["packs"]}
    assert set(MEMBER_TITLES) <= ids, set(MEMBER_TITLES) - ids
