"""The demo seeder's generated PDFs must be well-formed: the cross-reference
table has to point at each object, or viewers show a repair warning."""

import re

from app.scripts.seed_workspace import simple_pdf


def test_simple_pdf_is_well_formed():
    data = simple_pdf("Proposal (v2) – Northwind", ["Line one", "", r"Back\slash and (parens)"])
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
    assert data[start:start + 4] == b"xref"
    offsets = [int(m) for m in re.findall(rb"(\d{10}) 00000 n ", data)]
    assert len(offsets) == 6
    for number, offset in enumerate(offsets, start=1):
        assert data[offset:].startswith(f"{number} 0 obj".encode())
    length = int(re.search(rb"/Length (\d+)", data).group(1))
    stream = data.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0]
    assert len(stream) == length
    # Text is escaped and non-ASCII is replaced rather than corrupting the stream.
    assert rb"\(v2\)" in stream and rb"Back\\slash" in stream and b"?" in stream
