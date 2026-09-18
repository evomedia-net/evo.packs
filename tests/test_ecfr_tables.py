"""eCFR tables keep their rows, and every cell keeps its column (evo.packs#7).

eCFR serves tables as HTML - <TABLE>/<THEAD>/<TR>/<TD> - not the CFR-native
<ROW>/<ENT> that to_text already handled. In 49 CFR 172.101 alone there are
6,402 <TR> and 57,789 <TD> and not one <ROW>, so every table tag fell through
to the catch-all `<[^>]+>` -> ' ' and the Hazardous Materials Table arrived as
an unlabelled stream of cell values:

    "...10 percent nitroglycerin, by mass\\r 4.1\\r UN3319\\r II\\r 118\\r None"

Downstream that made a substance name un-answerable. Retrieval scored those
fragments above the prose in 173/177 that actually answers a handling
question - they contain the substance name, after all - the model was handed
table cells, and it refused. Asking the same thing without naming a
substance ("what about transferring explosives?") answered correctly from
177.835(j), which is what proved the corpus held the answer and the chunking
was hiding it.
"""
from evopacks.collect import ecfr


# The shape eCFR actually serves: a two-tier header where some columns span
# both rows and others are groups whose sub-headers sit in the second row.
HMT = """<TABLE>
<THEAD>
<TR>
<TH rowspan="2">(1)<br></br>Symbols</TH>
<TH rowspan="2">(2)<br></br>Hazardous materials descriptions and proper shipping names</TH>
<TH rowspan="2">(3)<br></br>Hazard class or Division</TH>
<TH rowspan="2">(4)<br></br>Identification Numbers</TH>
<TH colspan="3">(8)<br></br>Packaging<br></br>(&#167; 173.***)</TH>
</TR>
<TR>
<TH>Exceptions<br></br>(8A)</TH>
<TH>Non-bulk<br></br>(8B)</TH>
<TH>Bulk<br></br>(8C)</TH>
</TR>
</THEAD>
<TBODY>
<TR>
<TD> </TD>
<TD>Acetal</TD>
<TD>3</TD>
<TD>UN1088</TD>
<TD>150</TD>
<TD>202</TD>
<TD>242</TD>
</TR>
<TR>
<TD>A</TD>
<TD>Nitroglycerin, liquid, not desensitized</TD>
<TD>Forbidden</TD>
<TD></TD>
<TD></TD>
<TD></TD>
<TD></TD>
</TR>
<TR>
<TD></TD><TD></TD><TD></TD><TD></TD><TD></TD><TD></TD><TD></TD>
</TR>
</TBODY>
</TABLE>"""


def _rows(text: str) -> list[str]:
    return [l for l in text.split("\n") if l.strip()]


def test_a_row_stays_one_row():
    """The defect: 12 cells became 12 lines with nothing tying them together."""
    out = ecfr.to_text(HMT)
    acetal = [l for l in _rows(out) if "Acetal" in l]
    assert len(acetal) == 1, f"the row was split across lines: {acetal}"


def test_every_cell_carries_its_column():
    line = next(l for l in _rows(ecfr.to_text(HMT)) if "Acetal" in l)
    assert "Hazardous materials descriptions and proper shipping names: Acetal" in line
    assert "Hazard class or Division: 3" in line
    assert "Identification Numbers: UN1088" in line


def test_a_column_group_keeps_both_halves_of_its_label():
    """"202" alone is meaningless; "Packaging - Non-bulk: 202" is an answer."""
    line = next(l for l in _rows(ecfr.to_text(HMT)) if "Acetal" in l)
    assert "Packaging - Exceptions: 150" in line
    assert "Packaging - Non-bulk: 202" in line
    assert "Packaging - Bulk: 242" in line


def test_the_reported_row_reads_as_an_answer():
    """Undesensitized liquid nitroglycerin is forbidden in transport - the
    single most important thing to say about moving it, and it was a bare
    "Forbidden" floating between unrelated cells."""
    line = next(l for l in _rows(ecfr.to_text(HMT)) if "Nitroglycerin" in l)
    assert "Symbols: A" in line
    assert "Hazardous materials descriptions and proper shipping names: Nitroglycerin, liquid, not desensitized" in line
    assert "Hazard class or Division: Forbidden" in line


def test_empty_cells_are_dropped_not_rendered_as_bare_labels():
    """The HMT is mostly empty cells. A row of labels with nothing after them
    matches every query equally and answers none."""
    line = next(l for l in _rows(ecfr.to_text(HMT)) if "Nitroglycerin" in l)
    assert "Packaging" not in line, f"empty packaging cells were rendered: {line}"
    assert ": |" not in line and not line.rstrip().endswith(":")


def test_an_entirely_empty_row_is_dropped():
    out = ecfr.to_text(HMT)
    assert len(_rows(out)) == 2, f"expected 2 data rows, got {_rows(out)}"


def test_column_labels_drop_the_grid_only_furniture():
    """The column number, the cross-reference and the letter tag are identical
    on every row: they cost tokens in every chunk and say nothing."""
    out = ecfr.to_text(HMT)
    assert "(1)" not in out and "(8A)" not in out
    assert "§ 173.***" not in out
    assert "Symbols:" in out, "the label itself is kept"


def test_prose_around_a_table_is_untouched():
    xml = ("<DIV8><HEAD>&#167; 172.101 Purpose</HEAD>"
           "<P>(a) The Table designates the materials listed therein.</P>"
           + HMT +
           "<P>(b) Column 1 contains six symbols.</P></DIV8>")
    out = ecfr.to_text(xml)
    assert "172.101 Purpose" in out
    assert "(a) The Table designates the materials listed therein." in out
    assert "(b) Column 1 contains six symbols." in out
    assert "Hazard class or Division: 3" in out, "and the table is still rendered"


def test_the_cfr_native_row_markup_still_works():
    """<ROW>/<ENT> is the other shape eCFR serves; it was never broken and
    must not become broken."""
    xml = "<DIV8><HEAD>&#167; 172.101 Purpose</HEAD><P>Intro</P><ROW><ENT>a</ENT><ENT>b</ENT></ROW></DIV8>"
    t = ecfr.to_text(xml)
    assert "172.101 Purpose" in t and "a | b" in t


def test_a_table_without_a_header_still_keeps_its_rows():
    """Some CFR tables carry no THEAD. Unlabelled is acceptable; merged rows
    are not."""
    xml = "<TABLE><TBODY><TR><TD>x</TD><TD>y</TD></TR><TR><TD>p</TD><TD>q</TD></TR></TBODY></TABLE>"
    rows = _rows(ecfr.to_text(xml))
    assert rows == ["x | y", "p | q"]
