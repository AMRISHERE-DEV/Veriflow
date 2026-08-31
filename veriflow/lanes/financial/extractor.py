"""Deterministic Lane-A extraction from an EDGAR companyfacts record.

companyfacts shape (data.sec.gov/api/xbrl/companyfacts/CIK##########.json):
  facts -> "us-gaap" -> <concept> -> "units" -> <unit> -> [ {val,accn,fy,fp,form,start,end,frame}, ... ]

No LLM. No network. Operates on an already-fetched dict (or a test fixture).
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from .models import SecFact


def extract_facts(companyfacts: dict, concept: str, unit: str = "USD",
                  taxonomy: str = "us-gaap") -> list[SecFact]:
    """Every authoritative datapoint for (concept, unit), each carrying its accession."""
    node = (((companyfacts or {}).get("facts", {}) or {}).get(taxonomy, {}) or {}).get(concept)
    if not node:
        return []
    rows = ((node.get("units", {}) or {}).get(unit, []) or [])
    out: list[SecFact] = []
    for r in rows:
        if not isinstance(r, dict) or "val" not in r or "accn" not in r:
            continue
        try:
            val = float(r["val"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue
        out.append(SecFact(
            concept=concept, value=val, unit=unit,
            fiscal_year=r.get("fy"), fiscal_period=r.get("fp"), form=r.get("form"),
            start=r.get("start"), end=r.get("end"), frame=r.get("frame"),
            accession=str(r["accn"]),
        ))
    return out


def select_period(facts: Iterable[SecFact], fiscal_year: int | None,
                  fiscal_period: str | None = "FY", form: str | None = None) -> list[SecFact]:
    """Low-level normalized-label selector retained for compatibility.

    The verifier uses its stricter normalized/restatement-aware period matcher instead.
    Normalization is deliberately conservative: it removes surrounding whitespace
    and case differences but never treats FY and Q4 as interchangeable.
    """
    invalid_year = object()

    def normalize_year(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return invalid_year
        text = str(value).strip()
        if len(text) == 4 and text.isdigit():
            return int(text)
        return invalid_year

    def normalize_label(value):
        return None if value is None else str(value).strip().upper()

    wanted_year = normalize_year(fiscal_year)
    if wanted_year is invalid_year:
        return []
    wanted_period = normalize_label(fiscal_period)
    wanted_form = normalize_label(form)
    return [
        f for f in facts
        if (wanted_year is None or normalize_year(f.fiscal_year) == wanted_year)
        and (wanted_period is None or normalize_label(f.fiscal_period) == wanted_period)
        and (wanted_form is None or normalize_label(f.form) == wanted_form)
    ]
