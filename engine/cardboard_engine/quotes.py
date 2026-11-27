"""Issued quotes and invoices (#47).

An issued quote is a frozen copy of the engine's quote: the next version of
the project's quote number (Q-0007 v1, v2, ...), with its validity date,
terms, GST, client and company details. It is kept as the exact JSON text it
was issued with, and saved as ``quotes/<number>/v<N>.json``, so it never
changes, whatever happens to the design afterwards.

An invoice is made from one quote version: a deposit or progress invoice for
a percentage, or a final invoice for what is left. It carries that version's
lines, and is saved as ``invoices/<number>.json`` with a CSV row for the
accounting package. Every amount is worked out here, to the cent.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

QUOTE_SCHEMA = "foldtheory.quote-version/1"
INVOICE_SCHEMA = "foldtheory.invoice/1"
QUOTES_DIR = "quotes"
INVOICES_DIR = "invoices"
KINDS = ("deposit", "progress", "final")
QUOTE_NUMBER = re.compile(r"^Q-\d{4,}$")
INVOICE_NUMBER = re.compile(r"^INV-\d{4,}$")
MAX_VERSIONS = 200
MAX_INVOICES = 200


class QuoteError(ValueError):
    """A quote or invoice that cannot be made. The message is for Claude and the designer."""


def cents(x: float) -> float:
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def quote_number(n: int) -> str:
    return f"Q-{int(n):04d}"


def invoice_number(n: int) -> str:
    return f"INV-{int(n):04d}"


def _line(quote: dict, key: str) -> Optional[float]:
    return next((l["value"] for l in quote.get("lines", []) if l["key"] == key), None)


def issue(quote: dict, number: str, version: int, *, today: datetime.date, valid_days: int, terms: str,
          company: Dict[str, str], client: Dict[str, str]) -> dict:
    """A quote version from the engine's quote (``DesignSession.last_quote``)."""
    if not quote or quote.get("total") is None:
        raise QuoteError("There is no quote to issue yet: run estimate_cost first")
    shown = [{k: l[k] for k in ("key", "label", "role", "value", "unit")}
             for l in quote["lines"] if l.get("role") != "working"]
    return {"schema": QUOTE_SCHEMA, "number": number, "version": version, "issued_on": today.isoformat(),
            "valid_until": (today + datetime.timedelta(days=valid_days)).isoformat(),
            "currency": quote.get("currency", "AUD"), "company": dict(company), "client": dict(client),
            "terms": terms, "lines": shown, "subtotal": quote.get("subtotal"), "gst": _line(quote, "gst") or 0.0,
            "total": quote["total"], "quote": quote}


def frozen_text(record: dict) -> str:
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"


def quote_path(record: dict) -> str:
    return f"{QUOTES_DIR}/{record['number']}/v{record['version']}.json"


def invoice_paths(record: dict) -> List[str]:
    return [f"{INVOICES_DIR}/{record['number']}.json", f"{INVOICES_DIR}/{record['number']}.csv"]


def read_quote(text: str, where: str) -> dict:
    r = _read(text, where, QUOTE_SCHEMA)
    if not isinstance(r.get("number"), str) or not QUOTE_NUMBER.match(r["number"]) or \
            not isinstance(r.get("version"), int) or r["version"] < 1 or not isinstance(r.get("total"), (int, float)):
        raise QuoteError(f"{where} is not a complete quote version")
    return r


def read_invoice(text: str, where: str) -> dict:
    r = _read(text, where, INVOICE_SCHEMA)
    if not isinstance(r.get("number"), str) or not INVOICE_NUMBER.match(r["number"]) or \
            not isinstance(r.get("quote"), dict) or not isinstance(r.get("amount"), (int, float)):
        raise QuoteError(f"{where} is not a complete invoice")
    return r


def _read(text: str, where: str, schema: str) -> dict:
    try:
        r = json.loads(text)
    except ValueError:
        raise QuoteError(f"{where} is not valid JSON") from None
    if not isinstance(r, dict) or r.get("schema") != schema:
        raise QuoteError(f"{where} is not a {schema} file")
    return r


def make_invoice(version: dict, kind: str, percent: Optional[float], previous: List[dict], number: str, *,
                 today: datetime.date, due_days: int) -> dict:
    """An invoice from one quote version. ``previous``: invoices already made from the same quote number."""
    if kind not in KINDS:
        raise QuoteError(f"kind must be one of {', '.join(KINDS)}")
    total, gst = float(version["total"]), float(version.get("gst") or 0.0)
    ex_total = cents(total - gst)
    done_ex = cents(sum(i["amount_ex_gst"] for i in previous))
    done_gst = cents(sum(i["gst"] for i in previous))
    left_ex, left_gst = cents(ex_total - done_ex), cents(gst - done_gst)
    label = f"quote {version['number']} v{version['version']}"
    if kind == "final":
        if percent is not None:
            raise QuoteError("A final invoice is for what is left of the quote; leave percent out")
        if cents(left_ex + left_gst) <= 0:
            raise QuoteError(f"Nothing left to invoice on {label}: earlier invoices cover the whole quote")
        ex, tax = left_ex, left_gst
        description = f"Final invoice for {label}"
    else:
        if percent is None:
            raise QuoteError(f"A {kind} invoice needs percent, the share of the quote to invoice (e.g. 30)")
        if not 0 < percent <= 100:
            raise QuoteError("percent must be more than 0 and at most 100")
        ex, tax = cents(ex_total * percent / 100), cents(gst * percent / 100)
        if ex > left_ex + 1e-9 or tax > left_gst + 1e-9:
            raise QuoteError(f"That would invoice more than is left of {label} "
                             f"({cents(left_ex + left_gst):.2f} {version.get('currency', '')} left)".rstrip())
        description = f"{kind.capitalize()} {_pct(percent)}% of {label}"
    return {"schema": INVOICE_SCHEMA, "number": number, "kind": kind, "percent": percent,
            "quote": {"number": version["number"], "version": version["version"]},
            "issued_on": today.isoformat(), "due_on": (today + datetime.timedelta(days=due_days)).isoformat(),
            "currency": version.get("currency", "AUD"), "company": version.get("company", {}),
            "client": version.get("client", {}), "description": description, "lines": version["lines"],
            "quote_total": total, "amount_ex_gst": ex, "gst": tax, "amount": cents(ex + tax)}


def _pct(p: float) -> str:
    return str(int(p)) if float(p).is_integer() else f"{p:g}"


def invoice_csv(inv: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["invoice_number", "issued_on", "due_on", "contact", "reference", "description", "amount_ex_gst",
                "gst", "amount", "currency"])
    q = inv["quote"]
    w.writerow([inv["number"], inv["issued_on"], inv["due_on"], inv.get("client", {}).get("client_name", ""),
                f"{q['number']} v{q['version']}", inv["description"], f"{inv['amount_ex_gst']:.2f}",
                f"{inv['gst']:.2f}", f"{inv['amount']:.2f}", inv["currency"]])
    return buf.getvalue()
