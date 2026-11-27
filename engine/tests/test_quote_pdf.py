"""Quote and invoice PDFs and the Quote view (#47, part 3).

Every issued quote version and invoice is also saved as a PDF made only from
the engine's numbers: the same values as its JSON, never typed in. The PDF
is written by the engine itself (no extra dependency) and is the same bytes
every time, so a frozen version's PDF never changes either. The Quote tab
shows the quote lines, the issued versions and the invoices, with an Issue
quote button.
"""

import json
import os
import re

import pytest

from cardboard_engine.design import DesignSession
from cardboard_engine.pdf import TextPdf, pdf_text
from cardboard_engine.quotes import invoice_pdf, quote_pdf
from cardboard_engine.views import ACTIONS


@pytest.fixture
def session(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("sheet_price", 38.5, scope="project", set_by="test")
    s.settings.set("machine_rate", 90, scope="project", set_by="test")
    s.settings.set("margin_pct", 35, scope="project", set_by="test")
    s.settings.set("next_quote_number", 7, scope="all", set_by="test")
    s.settings.set("company_name", "Fold Theory", scope="all", set_by="test")
    s.settings.set("company_abn", "12 345 678 901", scope="all", set_by="test")
    s.settings.set("client_name", "Acme (Events)", scope="project", set_by="test")
    s.settings.set("quote_terms", "50% deposit on acceptance; balance on delivery.", scope="all", set_by="test")
    return s


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def issued(session):
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=400, height_mm=450)
    run(session, "waffle_slice", x_ribs=3, y_ribs=3, thickness_mm=6)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=1000)
    run(session, "estimate_cost")
    run(session, "issue_quote")
    return session.quote_versions[-1]["record"]


# ---- the PDF writer -------------------------------------------------------------


def test_text_pdf_is_a_valid_deterministic_pdf():
    doc = TextPdf()
    doc.heading("QUOTE")
    doc.line("Board (3 sheets)", "115.50")
    doc.text("Brackets ( and ) and a backslash \\ are escaped")
    data = doc.render()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert data == doc.render()
    m = re.search(rb"startxref\s+(\d+)", data)
    assert data[int(m.group(1)):].startswith(b"xref")
    text = pdf_text(data)
    assert "QUOTE" in text and "115.50" in text
    assert "Brackets ( and ) and a backslash \\ are escaped" in text


def test_long_documents_get_more_pages():
    doc = TextPdf()
    for i in range(150):
        doc.line(f"Line {i}", f"{i}.00")
    data = doc.render()
    assert data.count(b"/Type /Page ") >= 3
    assert "Line 149" in pdf_text(data)


# ---- quote and invoice PDFs -------------------------------------------------------


def test_quote_pdf_numbers_are_the_quote_json_numbers(session):
    q = issued(session)
    text = pdf_text(quote_pdf(q))
    assert "Q-0007 v1" in text
    assert "Fold Theory" in text and "ABN 12 345 678 901" in text
    assert "Acme (Events)" in text
    assert f"Valid until {q['valid_until']}" in text
    assert "50% deposit on acceptance; balance on delivery." in text
    for line in q["lines"]:
        assert line["label"] in text
        assert f"{line['value']:.2f}" in text, line
    assert f"Total {q['total']:.2f} {q['currency']}" in " ".join(text.split())


def test_invoice_pdf_numbers_are_the_invoice_json_numbers(session):
    issued(session)
    run(session, "make_invoice", kind="deposit", percent=30)
    inv = session.invoices[-1]["record"]
    text = " ".join(pdf_text(invoice_pdf(inv)).split())
    assert "TAX INVOICE" in text and inv["number"] in text
    assert "Deposit 30% of quote Q-0007 v1" in text
    assert f"Due {inv['due_on']}" in text
    assert f"{inv['amount_ex_gst']:.2f}" in text and f"{inv['gst']:.2f}" in text
    assert f"Amount due {inv['amount']:.2f} {inv['currency']}" in text


def test_save_writes_the_pdfs_and_a_frozen_pdf_never_changes(session):
    issued(session)
    run(session, "make_invoice", kind="deposit", percent=30)
    folder = session.save_project(name="Acme stool")["folder"]
    qpdf = os.path.join(folder, "quotes", "Q-0007", "v1.pdf")
    ipdf = os.path.join(folder, "invoices", "INV-0001.pdf")
    before = open(qpdf, "rb").read()
    assert before.startswith(b"%PDF") and open(ipdf, "rb").read().startswith(b"%PDF")
    record = json.loads(open(os.path.join(folder, "quotes", "Q-0007", "v1.json")).read())
    assert f"{record['total']:.2f}" in pdf_text(before)
    run(session, "scale_model", height_mm=600)
    run(session, "waffle_slice", x_ribs=3, y_ribs=3, thickness_mm=6)
    run(session, "estimate_cost")
    run(session, "issue_quote")
    session.save_project(name="Acme stool")
    assert open(qpdf, "rb").read() == before
    assert os.path.isfile(os.path.join(folder, "quotes", "Q-0007", "v2.pdf"))


# ---- the Quote view ----------------------------------------------------------------


def test_quote_view_shows_versions_and_invoices(session):
    issued(session)
    run(session, "make_invoice", kind="deposit", percent=30)
    assert "issue_quote" in ACTIONS
    assert "quote" in [v["id"] for v in session.views.listing()]
    out = session.views.render("quote")
    versions = out["data"]["quotes.versions"]
    assert versions == [{"number": "Q-0007", "version": 1, "issued_on": versions[0]["issued_on"],
                         "valid_until": versions[0]["valid_until"], "total": session.last_quote["total"],
                         "currency": "AUD", "pdf": "quotes/Q-0007/v1.pdf"}]
    invoices = out["data"]["quotes.invoices"]
    assert invoices[0]["number"] == "INV-0001" and invoices[0]["pdf"] == "invoices/INV-0001.pdf"
    assert invoices[0]["reference"] == "Q-0007 v1"
