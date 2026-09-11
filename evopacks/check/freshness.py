"""Per-pack freshness methods. One monthly cadence for everything is wrong,
because the sources change in different ways:

  api_date        eCFR publishes `up_to_date_as_of` per title. Compare to the
                  pack's issue date. Cheap; weekly is free.
  manifest_hash   Re-enumerate the publisher's listing and diff the URL set.
                  New URLs are additions; URLs that vanished are a signal too
                  (withdrawn guidance), and a hash diff would never show them.
  new_identifier  OSHA publishes a revised directive under a NEW number - a
                  changed-hash check finds nothing. Diff the identifier set.
  harvest         EPA relocates PDFs. Only a full re-harvest of the topic pages
                  sees moved and newly-linked documents. Expensive: reported as
                  "run manually", never scheduled.

Every method returns the same shape so the CLI and a scheduler can treat them
alike: {pack, method, checked_at, stale: bool, added, removed, detail}.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from .. import config, http
from ..build.pack import load_specs
from ..collect import osha, osha_publications, nfpa_public


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _known_urls(root: Path, corpus: str, column: str = "source_url") -> set[str]:
    import csv
    f = root / corpus / "manifest.csv"
    if not f.exists():
        return set()
    with open(f, encoding="utf-8-sig") as fh:
        return {r[column].split("?")[0] for r in csv.DictReader(fh) if r.get(column)}


def api_date(spec: dict, captured: str) -> dict:
    r = spec["refresh"]
    raw = http.get(r["endpoint"])
    titles = json.loads(raw).get("titles", []) if raw else []
    cur = next((t.get(r["field"]) for t in titles if t.get("number") == r["title"]), None)
    stale = bool(cur and captured and cur > captured)
    return {"stale": stale, "added": 0, "removed": 0,
            "detail": f"eCFR title {r['title']} up_to_date_as_of={cur}; pack issue date={captured}"}


def manifest_diff(root: Path, corpus: str, current: set[str]) -> dict:
    known = _known_urls(root, corpus)
    added, removed = sorted(current - known), sorted(known - current)
    return {"stale": bool(added or removed), "added": len(added), "removed": len(removed),
            "detail": {"added_sample": added[:10], "removed_sample": removed[:10]}}


def check_pack(pack_id: str, root: Path | None = None, run_harvest: bool = False) -> dict:
    root = root or config.rag_docs()
    specs = load_specs()
    spec = specs.get(pack_id)
    if not spec:
        # member of a meta pack (e.g. cfr-29) - find the spec that owns it
        for s in specs.values():
            if pack_id in s.get("members", []):
                spec = s
                break
    if not spec:
        return {"pack": pack_id, "method": None, "checked_at": _now(), "stale": None,
                "detail": "unknown pack"}
    captures = {}
    st = config.state_dir() / "captures.json"
    if st.exists():
        captures = json.loads(st.read_text(encoding="utf-8"))
    res: dict = {"pack": pack_id, "checked_at": _now()}

    if pack_id in ("cfr-29", "dot-hazmat"):
        title = 29 if pack_id == "cfr-29" else 49
        s = {"refresh": {"endpoint": "https://www.ecfr.gov/api/versioner/v1/titles.json",
                         "field": "up_to_date_as_of", "title": title}}
        corpus = "CFR-29" if title == 29 else "CFR-49"
        res.update(method="api_date", **api_date(s, captures.get(corpus, "")))
    elif pack_id == "osha-publications":
        items = osha_publications.enumerate_catalog()          # listing uses no_cache already
        res.update(method="manifest_hash",
                   **manifest_diff(root, "EHS", {i["url"] for i in items}))
    elif pack_id == "osha-directives":
        ids = {u.rstrip("/").rsplit("/", 1)[-1].lower() for u in osha.enumerate_directives()}
        import csv
        with open(root / "OSHA-Directives" / "manifest.csv", encoding="utf-8-sig") as fh:
            known = {r["identifier"].lower() for r in csv.DictReader(fh)}
        added, removed = sorted(ids - known), sorted(known - ids)
        res.update(method="new_identifier", stale=bool(added), added=len(added),
                   removed=len(removed), detail={"new_directive_ids": added[:20]})
    elif pack_id == "osha-interpretations":
        urls = set(osha.enumerate_interpretations())
        res.update(method="manifest_hash", **manifest_diff(root, "OSHA-Interpretations", urls))
    elif pack_id == "nfpa-public":
        items = nfpa_public.harvest(nfpa_public.enumerate_pages())
        res.update(method="manifest_hash",
                   **manifest_diff(root, "NFPA-Public", {i["url"].split("?")[0] for i in items}))
    elif pack_id == "epa" or pack_id.startswith("epa-"):
        if not run_harvest:
            res.update(method="harvest", stale=None, added=0, removed=0,
                       detail="EPA needs a full re-harvest; run `evopacks check epa --run-harvest`")
        else:
            from ..collect import epa
            links = epa.harvest(epa.survey())
            res.update(method="harvest",
                       **manifest_diff(root, "EPA", {u for u in links}))
    else:
        res.update(method=None, stale=None, detail="no checker for this pack")

    (config.state_dir() / f"freshness_{pack_id}.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return res


def due(root: Path | None = None) -> list[str]:
    """Pack ids whose cadence has elapsed since their last check."""
    specs = load_specs()
    out = []
    for pid, spec in specs.items():
        cad = spec.get("refresh", {}).get("cadence_days")
        if not cad:
            continue
        # a meta pack's members are checked individually (each has its own method)
        ids = spec["members"] if spec.get("kind") == "meta" and "members" in spec else [pid]
        for mid in ids:
            if _is_due(mid, cad):
                out.append(mid)
    return out


def _is_due(pid: str, cad: int) -> bool:
    last = config.state_dir() / f"freshness_{pid}.json"
    if not last.exists():
        return True
    checked = json.loads(last.read_text(encoding="utf-8")).get("checked_at", "")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(checked)).days
    except ValueError:
        age = 10**6
    return age >= cad


