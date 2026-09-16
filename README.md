# evo.packs

Build, catalog, and freshness-check **content packs** for evo.ai — curated
regulatory corpora (OSHA, EPA, DOT hazmat, NFPA public material) that an
evo.ehs customer can install into Ask AI in one action. The pack system on the
evo.ai side is designed in
[evomedia-net/evo.ai#191](https://github.com/evomedia-net/evo.ai/issues/191).

This repo is the pipeline from publisher website to installable artifact. The
corpus it produces (~33 GB of source documents) never enters git — it lives
at `$RAG_DOCS` on the build machine. What ships is text: ~1.2 GB across every
pack, embedded at install time on the customer's evo.ai.

## Pipeline

```
collect  ->  extract  ->  ocr  ->  index  ->  build  ->  catalog
                                                 \-> check (freshness, report only)
```

| Stage | Does | Writes |
| --- | --- | --- |
| `collect <source>` | Fetch one publisher's corpus, politely, resumably | `$RAG_DOCS/<corpus>/` + `manifest.csv` |
| `extract` | Text layer for every document; classify what is usable | `$RAG_DOCS/_TEXT/`, `_extraction_audit.csv` |
| `ocr` | PyMuPDF + tesseract over what extract flagged image-only | `_TEXT/` (source PDFs untouched) |
| `index` | Embed/exclusion index + cross-corpus master index | `_TEXT_DEDUPE.csv`, `MASTER-INDEX.csv` |
| `build` | One `tar.zst` + `manifest.json` per pack, `embed=yes` text only | `dist/` |
| `catalog` | `packs.json` describing every built artifact | `packs.json` |
| `check` | Has the publisher changed anything since capture? | `state/freshness_*.json` |

Every stage is idempotent and resumable. A rate-limit block or a crash costs
the remaining work, not the done work.

## Quickstart

```bash
pip install -e ".[dev]"
export RAG_DOCS=C:\path\to\RAG-Docs      # PowerShell: $env:RAG_DOCS = "C:\path\to\RAG-Docs"

evopacks collect cfr-29                  # ~1 minute, 7 API calls
evopacks collect osha-interp             # ~45 minutes at the rate osha.gov tolerates
evopacks extract
evopacks ocr                             # needs tesseract on PATH or $TESSERACT_DIR
evopacks index
evopacks build
evopacks catalog --release-base https://github.com/evomedia-net/evo.packs/releases/download/v0.0.0.1.0
evopacks check --due
evopacks status
```

`pdftotext` (poppler) must be on PATH for `extract`. `ocr` needs tesseract and
PyMuPDF; it deliberately does **not** use ocrmypdf, whose ghostscript
dependency could not be installed on the build machine.

## Packs

Defined in `pack-specs/*.json`. Two kinds:

- **content** — one corpus (or one EPA section), ships text
- **meta** — a bundle: `members` install with it, `recommends` are offered

| Pack | Kind | Members / corpus | Rights |
| --- | --- | --- | --- |
| `osha` | meta | `cfr-29` *(required)*, `osha-interpretations`, `osha-directives`, `osha-publications` | public domain |
| `epa` | meta | one `epa-<section>` per EPA topic section (`epa-lead`, `epa-rmp`, `epa-rcra`, …) | public domain |
| `dot-hazmat` | content | 49 CFR 171–180 | public domain |
| `nfpa-public` | content | NFPA free tip sheets, lesson plans, FPRF research | NFPA copyright, free to redistribute with attribution |

Both meta-packs `recommend` `dot-hazmat`: OSHA governs a chemical in the
workplace, EPA released or disposed, DOT in transit, and hazmat questions
surface in all three contexts.

**Not a pack, on purpose:** NFPA's licensed standards (the incorporated-by-
reference editions on archive.org). Out of scope entirely.

**Never embedded:** text from spreadsheets. 321 XLSX files were 28% of all
extracted text and are monitoring dumps, not prose. `index` flags them
`bulk-data-spreadsheet`; `build` skips them.

## Sources and the traps in each

Numbers are from the 2026-08-31 capture.

| Source | Docs | How it is enumerated | What bit us |
| --- | --- | --- | --- |
| OSHA publications | 1,269 | `/publications/all`, one `views-row` per language | Per-link `title` attribute is unreliable — the `<b lang=>` marker is the truth |
| OSHA interpretations | 5,941 | listing ∪ sitemap | Listing omits 92; **CloudFront serves page 0 for every `?page=N`** without `no-cache`; WAF blocks ~5 req/s |
| OSHA directives | 788 | listing ∪ sitemap | Listing shows only current (377); **first PDF link on every page is the site nav's osha2254.pdf** — 205 wrong copies before dedupe caught it |
| 29 / 49 CFR | 794 / 1,050 | eCFR API, per part | **HTTP 406 without `Accept-Encoding`**; titles in `<HEAD>` not `<SUBJECT>`; `173.4a` breaks numeric sort |
| EPA | 12,835 | sitemap → 9,017 EHS pages → harvested links | **Sitemap has zero PDFs**; some assets 500 without `?rev=`; legacy `http://` hosts stall workers; `nepis.epa.gov` refused (robots: 1 req/10 s) |
| NFPA public | 737 | sitemap → `/downloadable-resources/` + research pages, EN + `/es/` | PDFs under robots-disallowed `/-/media/`; fetched from an enumerated list, not crawled |

All of that is encoded in `evopacks/config.py` (per-host rate policy) and
`evopacks/http.py` (shared backoff, magic-byte checks, short timeouts).

## Rights

`rights_tier` is a required field on every pack and travels into every
manifest. Two tiers exist:

- **`public-domain`** — US federal works, 17 U.S.C. § 105. EPA, OSHA, CFR.
- **`free-redistribution`** — `nfpa-public`. Attribute NFPA; don't resell.

## Freshness

`evopacks check` runs the method each pack declares and **reports only**:

| Method | Packs | Why |
| --- | --- | --- |
| `api_date` | cfr-29, dot-hazmat | eCFR publishes `up_to_date_as_of`; a date compare |
| `manifest_hash` | osha-publications, osha-interpretations, nfpa-public | Diff the URL set — a vanished URL is a signal a hash diff never shows |
| `new_identifier` | osha-directives | A revised CPL lands under a **new number**; hash diff finds nothing |
| `harvest` | epa | EPA relocates PDFs; only a re-harvest sees it. Manual (`--run-harvest`) |

## Layout

```
evopacks/
  config.py       $RAG_DOCS, per-host rate policy, forbidden hosts
  http.py         one polite fetch layer: backoff, cache, magic bytes
  collect/        ecfr, osha, osha_publications, epa, nfpa_public
  extract/        text (pdftotext/html/docx/xlsx), ocr (pymupdf+tesseract)
  index/          dedupe (embed index), master (MASTER-INDEX.csv)
  build/          pack (tar.zst + manifest.json), catalog (packs.json)
  check/          freshness
  cli.py
pack-specs/       one JSON per pack
ragf/             retrieval smoke-test cases for evo.ragframework
tests/            pure-function tests; no network
state/            per-run state (gitignored)
dist/             built artifacts (gitignored; published as GitHub Releases)
```

## Does the corpus actually retrieve?

Everything above measures *collection* quality — what was downloaded, what has
a text layer, what is duplicated. None of it measures whether a question gets
the right document back.

`ragf/corpus-smoke.jsonl` is 18 cases for
[evo.ragframework](https://github.com/evomedia-net/evo.ragframework), each a
question a real EHS user would ask, with a fact that is verifiably in the
corpus:

```bash
ragf validate ragf/corpus-smoke.jsonl
ragf run --adapter evoai --url http://localhost:8005 --token "$RAGF_TOKEN" \
         --tenant <tenant-uuid> --cases ragf/corpus-smoke.jsonl \
         --db results.db --label "packs v0.0.0.1.0"
```

The cases are chosen to fail loudly rather than quietly:

- **Facts, not vibes** — "10 working days" for the hepatitis B vaccine, "6 feet"
  for construction fall protection, "172.101" for the Hazardous Materials
  Table. A regex either finds it or it doesn't.
- **One case per corpus layer**, so a failure says *which* pack is not
  retrieving: rule text (`cfr-29`), interpretation letters, enforcement
  directives, EPA guidance, DOT hazmat, NFPA.
- **`interp-archived-flagged` is the important one.** It asks about a 1976
  interpretation and passes only if the answer signals the letter is archived
  and may no longer represent OSHA policy. 27% of the letters carry that
  notice; presenting one as current guidance is this corpus's main way of
  being confidently wrong.
- **`control-off-topic-gated`** asks the capital of France and expects a
  refusal — a corpus this large must not make the assistant bluff.

Run it against a tenant with the packs installed, before and after, and diff
with `ragf compare`. A pack changes retrieval for every existing question on
that tenant, so it can regress answers that work today.

## Versioning

Five segments, `v{major}.{rc}.{beta}.{alpha}.{build}` — `version.json` is
authoritative. Currently alpha. Pack artifact versions are separate and
date-stamped from the corpus capture (`2026.08.31`): a pack version names what
shipped, so two builds from one capture share a version.

## Licence

MIT — see [LICENSE](LICENSE).

That covers **this pipeline**. It does not cover the corpora it fetches:
each pack carries its own `rights_tier` and ships it in every manifest —
`public-domain` for the US federal material (EPA, OSHA, CFR, 17 U.S.C. § 105)
and `free-redistribution` for the NFPA public documents, which are NFPA
copyright, published free for redistribution with attribution and not for
resale. NFPA's licensed incorporated-by-reference standards are not
distributed here at all.
