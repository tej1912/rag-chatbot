from app.core.chunking import chunk_document


def test_empty_text_returns_no_chunks():
    assert chunk_document("") == []


def test_short_text_returns_single_chunk():
    text = "This is a short FAQ answer about our refund policy."
    chunks = chunk_document(text)
    assert len(chunks) == 1
    assert chunks[0].content == text


def test_faq_pairs_kept_intact():
    text = (
        "Q: What is your refund policy?\nA: 30 days, no questions asked.\n\n"
        "Q: How long does shipping take?\nA: 3-5 business days.\n"
    )
    chunks = chunk_document(text)
    assert len(chunks) == 2
    assert chunks[0].content.startswith("Q: What is your refund policy?")
    assert chunks[1].content.startswith("Q: How long does shipping take?")


def test_long_text_gets_split():
    paragraph = "This sentence repeats to pad out the document length. " * 200
    chunks = chunk_document(paragraph)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.content) > 0


def test_header_context_attached():
    text = "# Refund Policy\nWe offer full refunds within 30 days of purchase."
    chunks = chunk_document(text)
    assert any(c.header_context and "Refund Policy" in c.header_context for c in chunks)


def test_each_chunk_gets_its_own_sections_header():
    text = (
        "# Refund Policy\nFull refunds within 30 days.\n\n"
        "# Shipping\nStandard shipping takes 3-5 business days.\n\n"
        "# Privacy\nWe never sell your data."
    )
    chunks = chunk_document(text)
    assert [c.header_context for c in chunks] == ["Refund Policy", "Shipping", "Privacy"]
    assert "30 days" in chunks[0].content and "3-5" in chunks[1].content


def test_long_section_splits_but_every_piece_keeps_the_header():
    body = "This sentence pads the section so it must be split. " * 200
    chunks = chunk_document(f"# Big Section\n{body}\n\n# Small\nshort text")
    big = [c for c in chunks if c.header_context == "Big Section"]
    assert len(big) > 1
    assert [c.header_context for c in chunks][-1] == "Small"


def test_preamble_before_first_header_has_no_header():
    chunks = chunk_document("Intro text before any header.\n\n# Pricing\nFree and Premium.")
    assert chunks[0].header_context is None and chunks[1].header_context == "Pricing"


def test_faq_pairs_inside_a_section_stay_intact():
    text = "# FAQ\nQ: Is it free?\nA: Yes.\n\nQ: Can I cancel?\nA: Anytime."
    chunks = chunk_document(text)
    assert len(chunks) == 2 and all(c.header_context == "FAQ" for c in chunks)


def test_pdf_style_page_headers():
    chunks = chunk_document("# Page 1\nfirst page text\n\n# Page 2\nsecond page text")
    assert [c.header_context for c in chunks] == ["Page 1", "Page 2"]
