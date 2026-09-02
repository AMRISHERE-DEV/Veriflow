"""LIVE EDGAR adapter (stdlib urllib). The H1 boundary: it fetches AUTHORITATIVE
companyfacts bytes so the verifier never trusts model-supplied content.

NOT used in unit tests (tests inject a fixture dict). SEC requires a descriptive
User-Agent. Fails open: any error -> None (no partial/forged data ever admitted).
"""
from __future__ import annotations

import json
import os
import urllib.request

from .models import issue_trusted_companyfacts

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_MAX_COMPANYFACTS_BYTES = 25 * 1024 * 1024
_MAX_TICKERS_BYTES = 5 * 1024 * 1024


def _read_json_capped(resp, *, max_bytes: int) -> dict:
    raw = resp.read(max_bytes + 1)
    if raw is None or len(raw) > max_bytes:
        raise ValueError("SEC response exceeded byte limit")
    payload = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise ValueError("SEC response must be a JSON object")
    return payload


def _validated_user_agent(user_agent: str | None) -> str:
    configured = user_agent if user_agent is not None else os.environ.get("SEC_USER_AGENT")
    value = (configured or "").strip()
    lowered = value.lower()
    if (len(value) < 12 or "replace" in lowered or "set-your-email" in lowered
            or "example.com" in lowered):
        raise ValueError("set SEC_USER_AGENT to a descriptive application and contact identity")
    return value


def _validated_timeout(timeout: float) -> float:
    value = float(timeout)
    if not 0 < value <= 300:
        raise ValueError("timeout must be greater than zero and at most 300 seconds")
    return value


def _validated_cik(cik: object) -> int:
    value = str(cik).strip().upper()
    if value.startswith("CIK"):
        value = value[3:]
    if not value.isdecimal() or len(value) > 10:
        raise ValueError("CIK must contain at most ten decimal digits")
    cik_int = int(value)
    if not 1 <= cik_int <= 9_999_999_999:
        raise ValueError("CIK is outside the SEC identifier range")
    return cik_int


def fetch_companyfacts(
    cik: object,
    *,
    user_agent: str | None = None,
    timeout: float = 20.0,
) -> dict | None:
    """Fetch the authoritative companyfacts record for a CIK. Returns None on any failure."""
    try:
        cik_int = _validated_cik(cik)
        agent = _validated_user_agent(user_agent)
        bounded_timeout = _validated_timeout(timeout)
        req = urllib.request.Request(  # noqa: S310 - fixed SEC HTTPS endpoint.
            _FACTS_URL.format(cik=cik_int), headers={"User-Agent": agent})
        with urllib.request.urlopen(  # noqa: S310 - fixed SEC HTTPS endpoint.
            req, timeout=bounded_timeout
        ) as resp:
            # The live EDGAR fetch IS the authoritative-provenance boundary: wrap the bytes as trusted.
            data = _read_json_capped(resp, max_bytes=_MAX_COMPANYFACTS_BYTES)
            return issue_trusted_companyfacts(data, issuer="edgar")
    except Exception:
        return None


def ticker_to_cik(
    ticker: str,
    *,
    user_agent: str | None = None,
    timeout: float = 20.0,
) -> str | None:
    """Resolve a ticker to a zero-padded 10-digit CIK via SEC's official mapping."""
    try:
        agent = _validated_user_agent(user_agent)
        bounded_timeout = _validated_timeout(timeout)
        req = urllib.request.Request(  # noqa: S310 - fixed SEC HTTPS endpoint.
            _TICKERS_URL, headers={"User-Agent": agent})
        with urllib.request.urlopen(  # noqa: S310 - fixed SEC HTTPS endpoint.
            req, timeout=bounded_timeout
        ) as resp:
            data = _read_json_capped(resp, max_bytes=_MAX_TICKERS_BYTES)
        want = (ticker or "").strip().upper()
        if not want or len(want) > 32:
            return None
        for row in data.values():
            if isinstance(row, dict) and str(row.get("ticker", "")).upper() == want:
                return f"{int(row['cik_str']):010d}"
    except Exception:
        return None
    return None
