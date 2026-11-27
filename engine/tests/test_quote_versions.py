"""Issued quotes and invoices (#47, part 2).

``issue_quote`` freezes the engine's quote as the next version of the
project's quote number (Q-0007 v1, v2, ...) with its validity date, terms,
GST and company details. A frozen version is never changed. ``make_invoice``
makes a deposit, progress or final invoice from one quote version, with its
lines, and the invoice exports CSV for the accounting package. Numbers come
from settings (next_quote_number, next_invoice_number), so they carry on
from the studio's own numbering.
"""

import csv
import datetime
import io
import json
import os

import pytest

from cardboard_engine.design import DesignSession
from cardboard_engine.quotes import invoice_csv


@pytest.fixture
def session(tmp_path):
    s = DesignSession(home=str(tmp_path))
    s.settings.set("sheet_price", 38.5, scope="project", set_by="test")
    s.settings.set("machine_rate", 90, scope="project", set_by="test")
    s.settings.set("margin_pct", 35, scope="project", set_by="test")
    s.settings.set("next_quote_number", 7, scope="all", set_by="test")
    s.settings.set("next_invoice_number", 41, scope="all", set_by="test")
    s.settings.set("company_name", "Fold Theory", scope="all", set_by="test")
    s.settings.set("company_abn", "12 345 678 901", scope="all", set_by="test")
    s.settings.set("client_name", "Acme", scope="project", set_by="test")
    s.settings.set("quote_terms", "50% deposit on acceptance", scope="all", set_by="test")
    return s


def run(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert not r.is_error, r.text
    return r.text


def fails(session, tool_name, **args):
    r = session.run_tool(tool_name, args)
    assert r.is_error, r.text
    return r.text


def design(session, height=450):
    run(session, "create_shape", shape="box", width_mm=400, depth_mm=400, height_mm=height)
    run(session, "waffle_slice", x_ribs=3, y_ribs=3, thickness_mm=6)
    run(session, "nest_sheets", sheet_width_mm=1200, sheet_height_mm=1000)
    run(session, "estimate_cost")


def versions(session):
    return [(q["number"], q["version"]) for q in session.state()["quotes"]]


# ---- quotes --------------------------------------------------------------------


def test_issue_quote_numbers_and_versions(session):
    design(session)
    text = run(session, "issue_quote")
    assert "Q-0007 v1" in text
    assert f"{session.last_quote['total']:.2f}" in text
    design(session, height=500)
    assert "Q-0007 v2" in run(session, "issue_quote")
    assert versions(session) == [("Q-0007", 1), ("Q-0007", 2)]
    nxt = session.settings.resolve("next_quote_number")
    assert (nxt["value"], nxt["layer"]) == (8, "default"), "the next number is kept for all projects"


def test_a_quote_version_carries_what_the_issue_asks_for(session):
    design(session)
    run(session, "issue_quote")
    q = session.quote_versions[0]["record"]
    assert q["schema"] == "foldtheory.quote-version/1"
    assert q["company"] == {"name": "Fold Theory", "abn": "12 345 678 901"}
    assert q["client"]["client_name"] == "Acme"
    assert q["terms"] == "50% deposit on acceptance"
    issued = datetime.date.fromisoformat(q["issued_on"])
    assert datetime.date.fromisoformat(q["valid_until"]) == issued + datetime.timedelta(days=30)
    assert q["total"] == session.last_quote["total"]
    assert q["gst"] == next(l["value"] for l in session.last_quote["lines"] if l["key"] == "gst")
    assert [l["key"] for l in q["lines"]][-1] == "total"


def test_issue_needs_a_quote(session):
    run(session, "create_shape", shape="box")
    assert "estimate_cost" in fails(session, "issue_quote")


def test_a_frozen_version_is_never_changed(session):
    design(session)
    run(session, "issue_quote")
    folder = session.save_project(name="Acme stool")["folder"]
    path = os.path.join(folder, "quotes", "Q-0007", "v1.json")
    before = open(path, "rb").read()
    design(session, height=600)
    run(session, "issue_quote")
    session.save_project(name="Acme stool")
    assert open(path, "rb").read() == before
    assert os.path.isfile(os.path.join(folder, "quotes", "Q-0007", "v2.json"))


# ---- invoices ------------------------------------------------------------------


def test_invoice_from_v2_keeps_v2_lines_after_v3_exists(session):
    design(session)
    run(session, "issue_quote")
    design(session, height=500)
    run(session, "issue_quote")
    v2 = json.loads(json.dumps(session.quote_versions[1]["record"]))
    design(session, height=700)
    run(session, "issue_quote")
    text = run(session, "make_invoice", kind="final", version=2)
    inv = session.invoices[-1]["record"]
    assert inv["number"] == "INV-0041"
    assert inv["quote"] == {"number": "Q-0007", "version": 2}
    assert inv["lines"] == v2["lines"]
    assert inv["amount"] == v2["total"]
    assert "INV-0041" in text and "Q-0007 v2" in text


def test_deposit_then_final_add_up_to_the_quote_to_the_cent(session):
    design(session)
    run(session, "issue_quote")
    q = session.quote_versions[0]["record"]
    run(session, "make_invoice", kind="deposit", percent=30)
    dep = session.invoices[-1]["record"]
    ex_gst = round(q["total"] - q["gst"], 2)
    assert dep["amount_ex_gst"] == pytest.approx(round(ex_gst * 0.30 + 1e-9, 2))
    assert dep["gst"] == pytest.approx(round(q["gst"] * 0.30 + 1e-9, 2))
    assert dep["amount"] == pytest.approx(dep["amount_ex_gst"] + dep["gst"])
    run(session, "make_invoice", kind="final")
    fin = session.invoices[-1]["record"]
    assert fin["number"] == "INV-0042"
    assert round(dep["amount"] + fin["amount"], 2) == q["total"]
    assert round(dep["gst"] + fin["gst"], 2) == q["gst"]
    assert "nothing left" in fails(session, "make_invoice", kind="final").lower()


def test_progress_invoices_cannot_pass_the_quote(session):
    design(session)
    run(session, "issue_quote")
    run(session, "make_invoice", kind="progress", percent=60)
    assert "more than" in fails(session, "make_invoice", kind="progress", percent=50)
    assert "percent" in fails(session, "make_invoice", kind="deposit")


def test_due_date_from_payment_terms(session):
    design(session)
    run(session, "issue_quote")
    session.settings.set("payment_terms_days", 7, scope="project", set_by="test")
    run(session, "make_invoice", kind="deposit", percent=50)
    inv = session.invoices[-1]["record"]
    assert datetime.date.fromisoformat(inv["due_on"]) == (
        datetime.date.fromisoformat(inv["issued_on"]) + datetime.timedelta(days=7))


def test_invoice_csv_for_the_accounting_package(session):
    design(session)
    run(session, "issue_quote")
    run(session, "make_invoice", kind="deposit", percent=30)
    inv = session.invoices[-1]["record"]
    rows = list(csv.DictReader(io.StringIO(invoice_csv(inv))))
    assert len(rows) == 1
    r = rows[0]
    assert r["invoice_number"] == "INV-0041"
    assert r["contact"] == "Acme"
    assert r["description"] == "Deposit 30% of quote Q-0007 v1"
    assert float(r["amount_ex_gst"]) == inv["amount_ex_gst"]
    assert float(r["gst"]) == inv["gst"]
    assert float(r["amount"]) == inv["amount"]
    assert r["currency"] == "AUD"


# ---- save and open -------------------------------------------------------------


def test_save_and_reopen_restores_quotes_and_invoices(session, tmp_path):
    design(session)
    run(session, "issue_quote")
    run(session, "make_invoice", kind="deposit", percent=30)
    folder = session.save_project(name="Acme stool")["folder"]
    assert os.path.isfile(os.path.join(folder, "invoices", "INV-0041.json"))
    assert os.path.isfile(os.path.join(folder, "invoices", "INV-0041.csv"))
    fresh = DesignSession(home=str(tmp_path))
    fresh.open_project(folder)
    assert versions(fresh) == [("Q-0007", 1)]
    assert [i["number"] for i in fresh.state()["invoices"]] == ["INV-0041"]
    fresh.run_tool("estimate_cost", {})
    assert "Q-0007 v2" in fresh.run_tool("issue_quote", {}).text
    assert fresh.state()["project"]["unsaved_changes"] is True


def test_reset_clears_quotes(session):
    design(session)
    run(session, "issue_quote")
    session.reset()
    assert session.state()["quotes"] == [] and session.state()["invoices"] == []
