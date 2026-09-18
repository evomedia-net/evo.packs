"""Fetch CFR parts from the eCFR API, one text file per section.

Part-level calls, split locally: 7 calls for all of 29 CFR's OSHA parts
instead of 812 per-section requests. The API is official JSON/XML - no
scraping, no WAF, and every fetch is pinned to an issue date, so this is the
only corpus that can state precisely how current it is.

Gotchas, all hit while building this:
  * the full-text endpoint returns HTTP 406 unless Accept-Encoding permits
    compression (http.session() sets it)
  * section titles live in the XML <HEAD> element, not <SUBJECT>
  * 49 CFR has identifiers like 173.4a - a numeric sort key raises TypeError
  * [Reserved] sections carry no text and are skipped; the count therefore
    lands below the structure's section count, and that is correct
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path

from .. import config, http

API = "https://www.ecfr.gov/api/versioner/v1"

TITLES = {
    29: {
        "corpus": "CFR-29",
        "label": "Federal regulation (29 CFR)",
        "parts": {
            "1904": "Recording and Reporting Occupational Injuries and Illnesses",
            "1910": "Occupational Safety and Health Standards (General Industry)",
            "1915": "Occupational Safety and Health Standards for Shipyard Employment",
            "1917": "Marine Terminals",
            "1918": "Safety and Health Regulations for Longshoring",
            "1926": "Safety and Health Regulations for Construction",
            "1928": "Occupational Safety and Health Standards for Agriculture",
        },
        # Interpretation letters span 1972-2026 and construe the rule AS IT READ
        # AT THE TIME. Stamped into every file so the hazard travels with the data.
        "note": ("Current text. OSHA interpretation letters in ../OSHA-Interpretations/ "
                 "may interpret EARLIER wording of this section."),
    },
    49: {
        "corpus": "CFR-49",
        "label": "Federal regulation (49 CFR - DOT/PHMSA hazmat)",
        "parts": {
            "171": "General Information, Regulations, and Definitions",
            "172": "Hazardous Materials Table, Special Provisions, Communications, "
                   "Emergency Response Information, Training Requirements",
            "173": "Shippers - General Requirements for Shipments and Packagings",
            "174": "Carriage by Rail",
            "175": "Carriage by Aircraft",
            "176": "Carriage by Vessel",
            "177": "Carriage by Public Highway",
            "178": "Specifications for Packagings",
            "179": "Specifications for Tank Cars",
            "180": "Continuing Qualification and Maintenance of Packagings",
        },
        "note": "",
    },
}


def slug(s: str, n: int = 80) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = re.sub(r"[^\w\s\-.]", "", s, flags=re.U)
    return re.sub(r"[\s_]+", "-", s).strip("-")[:n].rstrip("-.") or "section"


def sort_key(section: str):
    """Natural order: 173.4 < 173.4a < 173.10.

    Each segment splits into (numeric prefix, alpha suffix), so an alpha
    suffix sorts right after its number instead of turning the whole segment
    into a string that lands after every integer.
    """
    key = []
    for seg in re.split(r"[.\-]", section):
        if not seg:
            continue
        m = re.match(r"(\d*)(.*)", seg)
        key.append((int(m.group(1)) if m.group(1) else -1, m.group(2)))
    return tuple(key)


def _flatten(s: str) -> str:
    """Tag soup to one clean line."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _column_label(raw: str) -> str:
    """A <TH> as a prefix worth repeating on every row of the column.

    Drops the column NUMBER ("(8)") and the cross-references and letter tags
    that only make sense while looking at the printed grid ("(\u00a7 172.102)",
    "(8A)"): they are identical on every row, so they cost tokens in every
    chunk and tell a reader nothing the label has not already said.
    """
    t = _flatten(raw)
    t = re.sub(r"^\(\d+\)\s*", "", t)                 # leading column number
    t = re.sub(r"\s*\((?:see\s+)?\u00a7+[^)]*\)", "", t)   # (\u00a7 172.102), (see \u00a7\u00a7 173.27 ...)
    t = re.sub(r"\s*\(\d+[A-Z]\)", "", t)              # (8A), (9B), (10A)
    return t.strip(" .")


def _header_labels(thead: str) -> list[str]:
    """One label per BODY column, expanding the two-tier header.

    A <TH rowspan="2"> spans both header rows and labels one column on its
    own; a <TH colspan="n"> is a group whose n sub-headers live in the second
    row, and each of those columns is labelled "Group - Sub" so a cell keeps
    both halves of its meaning ("Packaging - Non-bulk").
    """
    rows = re.findall(r"<TR[^>]*>(.*?)</TR>", thead, re.S)
    if not rows:
        return []
    top = re.findall(r"<TH([^>]*)>(.*?)</TH>", rows[0], re.S)
    sub = ([_column_label(c) for c in re.findall(r"<TH[^>]*>(.*?)</TH>", rows[1], re.S)]
           if len(rows) > 1 else [])
    labels: list[str] = []
    si = 0
    for attrs, text in top:
        m = re.search(r'colspan="(\d+)"', attrs, re.I)
        span = int(m.group(1)) if m else 1
        base = _column_label(text)
        if span == 1:
            labels.append(base)
            continue
        for _ in range(span):
            s2 = sub[si] if si < len(sub) else ""
            si += 1
            labels.append(f"{base} - {s2}" if s2 else base)
    return labels


def _render_table(tbl: str) -> str:
    """One line per row, every value carrying its column name.

    Empty cells are dropped rather than rendered as "Label:" with nothing
    after it - the Hazardous Materials Table is mostly empty cells, and a
    row of bare labels is noise that matches every query equally.
    """
    thead = re.search(r"<THEAD[^>]*>(.*?)</THEAD>", tbl, re.S)
    labels = _header_labels(thead.group(1)) if thead else []
    body = re.search(r"<TBODY[^>]*>(.*?)</TBODY>", tbl, re.S)
    scope = body.group(1) if body else re.sub(r"<THEAD[^>]*>.*?</THEAD>", "", tbl, flags=re.S)
    out = []
    for rh in re.findall(r"<TR[^>]*>(.*?)</TR>", scope, re.S):
        cells = [_flatten(c) for c in re.findall(r"<T[DH][^>]*>(.*?)</T[DH]>", rh, re.S)]
        parts = []
        for i, v in enumerate(cells):
            if not v:
                continue
            lab = labels[i] if i < len(labels) else ""
            parts.append(f"{lab}: {v}" if lab else v)
        if parts:
            out.append(" | ".join(parts))
    return "\n".join(out)


def to_text(xml: str) -> str:
    # Tables first, and removed from the stream once rendered: their markup is
    # HTML (<TR>/<TD>), not the CFR <ROW>/<ENT> the rules below know, so left
    # in place every table tag would fall through to the catch-all and the
    # grid would arrive as an unlabelled run of cell values (evo.packs#7).
    xml = re.sub(r"<TABLE[^>]*>.*?</TABLE>",
                 lambda m: "\n" + _render_table(m.group(0)) + "\n", xml, flags=re.S)
    t = re.sub(r"<(SECTNO|SUBJECT|HEAD)>(.*?)</\1>", r"\2\n", xml, flags=re.S)
    t = re.sub(r"</(P|HED|HD\d?|FP|DIV\d|ROW)>", "\n", t)
    t = re.sub(r"</(ENT|CELL)>", " | ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def issue_date(title: int) -> str:
    """eCFR's 'up to date as of' for a title - the corpus's currency stamp."""
    raw = http.get(f"{API}/titles.json")
    if not raw:
        raise RuntimeError("eCFR titles.json unavailable")
    for t in json.loads(raw).get("titles", []):
        if t.get("number") == title:
            return t["up_to_date_as_of"]
    raise RuntimeError(f"title {title} not in eCFR titles.json")


def collect(title: int, root: Path | None = None) -> dict:
    spec = TITLES[title]
    root = root or config.rag_docs()
    dest = root / spec["corpus"]
    sect = dest / "Sections"
    sect.mkdir(parents=True, exist_ok=True)
    issue = issue_date(title)
    rows = []

    for part, part_name in spec["parts"].items():
        xml = http.get(f"{API}/full/{issue}/title-{title}.xml?part={part}", timeout=(8, 600))
        if not xml:
            print(f"  {title} CFR {part}: FAILED", flush=True)
            continue
        n = 0
        for block in re.split(r'(?=<DIV8[^>]*TYPE="SECTION")', xml):
            m = re.match(r'<DIV8[^>]*N="([^"]+)"[^>]*TYPE="SECTION"', block)
            if not m:
                continue
            ident = m.group(1)
            hm = re.search(r"<HEAD>(.*?)</HEAD>", block, re.S)
            subject = ""
            if hm:
                subject = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", hm.group(1)))).strip()
                subject = re.sub(r"^§+\s*[\d.\-–\s]+", "", subject).strip(" .")
            body = to_text(block)
            if len(body) < 40:            # [Reserved]
                continue
            fn = f"{slug(ident)}__{slug(subject, 70)}.txt" if subject else f"{slug(ident)}.txt"
            src = f"https://www.ecfr.gov/current/title-{title}/part-{part}/section-{ident}"
            header = (f"Citation: {title} CFR {ident}\nSubject: {subject}\n"
                      f"Part: {title} CFR {part} - {part_name}\n"
                      f"Issue date: {issue} (eCFR 'up to date as of')\n"
                      f"Source: {src}\nType: {spec['label']}\n")
            if spec["note"]:
                header += f"Note: {spec['note']}\n"
            p = sect / fn
            p.write_text(header + "-" * 72 + "\n\n" + body + "\n", encoding="utf-8")
            rows.append({"relative_path": f"Sections/{fn}", "citation": f"{title} CFR {ident}",
                         "section": ident, "subject": subject, "part": part,
                         "part_name": part_name, "issue_date": issue, "source_url": src,
                         "publisher": "U.S. Government Publishing Office / eCFR",
                         "bytes": p.stat().st_size,
                         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
            n += 1
        print(f"  {title} CFR {part}: {n} sections", flush=True)

    rows.sort(key=lambda r: sort_key(r["section"]))
    with open(dest / "manifest.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    return {"corpus": spec["corpus"], "sections": len(rows), "issue_date": issue,
            "bytes": sum(r["bytes"] for r in rows)}
