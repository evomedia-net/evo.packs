"""Unit tests for the parts that don't need a network.

The directive-link test is the important one: it pins the defect where the
first recovery pass fetched osha.gov's site-wide nav PDF 205 times.
"""
from evopacks.collect import ecfr, epa, nfpa_public, osha, osha_publications


# ------------------------------------------------------------------ eCFR
def test_ecfr_sort_key_handles_mixed_alnum_ids():
    ids = ["173.4a", "173.4", "173.10", "173.2", "171.8"]
    assert sorted(ids, key=ecfr.sort_key) == ["171.8", "173.2", "173.4", "173.4a", "173.10"]


def test_ecfr_to_text_flattens_table_cells():
    xml = "<DIV8><HEAD>§ 172.101 Purpose</HEAD><P>Intro</P><ROW><ENT>a</ENT><ENT>b</ENT></ROW></DIV8>"
    t = ecfr.to_text(xml)
    assert "172.101 Purpose" in t and "a | b" in t


# ------------------------------------------------------------------ OSHA
def test_directive_pdf_link_never_returns_site_nav_pdf():
    page = ('<a href="/sites/default/files/publications/osha2254.pdf">Training</a>'
            '<a href="/sites/default/files/enforcement/directives/CPL_02-02-066.pdf">Download</a>')
    assert osha.directive_pdf_link(page, "cpl-02-02-066") == \
        "https://www.osha.gov/sites/default/files/enforcement/directives/CPL_02-02-066.pdf"


def test_directive_pdf_link_prefers_filename_matching_id():
    page = ('<a href="/sites/default/files/enforcement/directives/OTHER.pdf">x</a>'
            '<a href="/sites/default/files/enforcement/directives/CPL_02-02-066.pdf">y</a>')
    assert osha.directive_pdf_link(page, "cpl-02-02-066").endswith("CPL_02-02-066.pdf")


def test_directive_pdf_link_none_when_only_nav_pdf_present():
    page = '<a href="/sites/default/files/publications/osha2254.pdf">Training</a>'
    assert osha.directive_pdf_link(page, "adm-81a-ch-1") is None


def test_clean_letter_strips_disclaimer_and_preamble_keeps_archive_notice():
    body = ("Standard Number:\n\n1910.147\n\n" + osha.DISCLAIMER_FULL + " \n\n"
            + osha.ARCHIVE + ", and may no longer represent OSHA Policy.\n\nDear Mr. X,")
    out, archived = osha.clean_letter_body(body)
    assert osha.DISCLAIMER_START not in out
    assert not out.startswith("Standard Number")
    assert osha.ARCHIVE in out and archived is True
    # idempotent
    assert osha.clean_letter_body(out)[0] == out


def test_publications_language_normalisation():
    assert osha_publications._language("English", "English") == "English"
    assert osha_publications._language("es", "Español") == "Spanish"
    assert osha_publications._language("vi", "Tiếng Việt(Vietnamese)") == "Vietnamese"


def test_publications_asset_filter_rejects_index_pages_and_queries():
    ok = osha_publications._is_asset
    assert ok("PDF", "https://www.osha.gov/sites/default/files/publications/OSHA3713.pdf")
    assert ok("HTML", "https://www.osha.gov/publications/shib101003")
    assert not ok("PDF", "https://www.osha.gov/publications/bytype/posters")
    assert not ok("PDF", "https://www.osha.gov/publications/all?page=3")
    assert not ok("Add to cart", "https://www.osha.gov/publications/add/product/1")


# ------------------------------------------------------------------- EPA
def test_epa_scope_excludes_settlement_pages():
    assert epa.SETTLEMENT.search("https://www.epa.gov/enforcement/2001-consent-decree-nucor")
    assert not epa.SETTLEMENT.search("https://www.epa.gov/enforcement/hazardous-waste-policy")


def test_epa_section_of_page():
    assert epa._section("https://www.epa.gov/rmp/some-page") == "rmp"


# ------------------------------------------------------------------ NFPA
def test_nfpa_files_keeps_rev_query_and_skips_images():
    h = ('href="/-/media/Project/Storefront/Catalog/Files/Safety-tip-sheets/CandleSafetyTips.pdf'
         '?rev=2aa815a6" href="/-/media/Project/Storefront/Catalog/Images/x.jpg?h=440"')
    f = nfpa_public._files(h)
    assert f == ["/-/media/Project/Storefront/Catalog/Files/Safety-tip-sheets/CandleSafetyTips.pdf?rev=2aa815a6"]


def test_nfpa_title_strips_marketing_suffix():
    assert nfpa_public._title("<title>Candle Safety Tip Sheet. Download the free NFPA PDF.</title>") \
        == "Candle Safety Tip Sheet"
