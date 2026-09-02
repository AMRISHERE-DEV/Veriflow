#!/usr/bin/env python3
"""Project Saturn flagship demo: SEC/XBRL financial claim assurance.

Verifies three claims about Apple's FY2023 revenue against the authoritative
SEC EDGAR companyfacts record (live when SEC_USER_AGENT is set, otherwise a
bundled offline fixture) and prints the full evidence pack for each:

  1. exact filed figure + trusted claim binding   -> VERIFIED (Lane A, L3)
  2. off by $42 with the same trusted binding     -> REFUTED
  3. exact filed figure but NO binding proof      -> capped below VERIFIED

Claim 3 is the point: model agreement is never evidence. Even a perfect value
match cannot mint VERIFIED unless the text->concept binding was independently
attested. Stdlib only.

Usage (from the repository root):
  python examples/demo_sec_xbrl.py                # offline, deterministic
  SEC_USER_AGENT="MyApp my-contact@my-domain" python examples/demo_sec_xbrl.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import urllib.request

from project_saturn.lanes.financial import (
    FinancialClaim,
    issue_trusted_companyfacts,
    verify_financial_claim,
)
from project_saturn.verify.trust import issue_trusted_binding_proof

CIK = 320193                                  # Apple Inc.
CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
FILED_FY2023_REVENUE = 383_285_000_000.0      # as reported in accn 0000320193-23-000106
EDGAR_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK:010d}.json"
MAX_BYTES = 25 * 1024 * 1024

# Trimmed offline fixture: real values transcribed from Apple's FY2023 10-K,
# in the exact shape of data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json.
FIXTURE = {
    "cik": CIK,
    "entityName": "Apple Inc.",
    "facts": {"us-gaap": {CONCEPT: {"units": {"USD": [
        {"val": 383285000000, "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY",
         "form": "10-K", "start": "2022-09-25", "end": "2023-09-30", "frame": "CY2023"},
    ]}}}},
}


def load_companyfacts():
    """Live EDGAR when a proper User-Agent is configured; bundled fixture otherwise.

    SEC policy requires a descriptive User-Agent naming the application and a
    contact address, so this demo never sends a placeholder: without
    SEC_USER_AGENT it stays offline. Both branches end at the same trust
    boundary - issue_trusted_companyfacts is the sole issuance point, and a
    plain dict handed to the verifier can never mint VERIFIED.
    """
    agent = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if len(agent) >= 12:
        try:
            req = urllib.request.Request(EDGAR_URL, headers={"User-Agent": agent})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(MAX_BYTES + 1)
            data = json.loads(raw.decode("utf-8", "replace")) if len(raw) <= MAX_BYTES else None
            if isinstance(data, dict):
                print(f"[source] live EDGAR companyfacts for CIK {CIK:010d}\n")
                return issue_trusted_companyfacts(data, issuer="edgar")
        except Exception as exc:
            print(f"[source] live EDGAR fetch failed ({exc!r}); using bundled fixture\n")
    else:
        print("[source] SEC_USER_AGENT not set; using bundled offline fixture\n")
    return issue_trusted_companyfacts(FIXTURE, issuer="audited-fixture")


def make_claim(claim_id: str, text: str, value: float, *, attested: bool) -> FinancialClaim:
    claim = FinancialClaim(
        claim_id=claim_id, text=text, concept=CONCEPT, claimed_value=value,
        unit="USD", cik=f"{CIK:010d}", ticker="AAPL",
        fiscal_year=2023, fiscal_period="FY",
    )
    if not attested:
        return claim  # no binding proof: even an exact value match caps below VERIFIED
    proof = issue_trusted_binding_proof(
        "human", True, "reviewer attests this prose refers to us-gaap FY2023 revenue",
        issuer="demo-reviewer", subject=claim.binding_subject())
    return dataclasses.replace(claim, binding_proof=proof)


def show(pack) -> None:
    print(f"== {pack.claim_id}: {pack.status.upper()}  (tier {pack.tier.value}, lane {pack.lane})")
    print(f"   wording : {pack.kernel_wording}")
    for reason in pack.reasons:
        print(f"   reason  : {reason}")
    if pack.as_reported_in:
        print(f"   filing  : accession {pack.as_reported_in}")
    print(f"   receipt : manifest sha256 {pack.manifest_hash}\n")


def main() -> None:
    facts = load_companyfacts()
    claims = [
        make_claim("true-claim",
                   "Apple reported total net sales of $383.285 billion for fiscal 2023.",
                   FILED_FY2023_REVENUE, attested=True),
        make_claim("off-by-dollars",
                   "Apple reported total net sales of $383,285,000,042 for fiscal 2023.",
                   FILED_FY2023_REVENUE + 42.0, attested=True),
        make_claim("no-binding-proof",
                   "Apple reported total net sales of $383.285 billion for fiscal 2023.",
                   FILED_FY2023_REVENUE, attested=False),
    ]
    for claim in claims:
        show(verify_financial_claim(claim, facts))
    print("Invariant demonstrated: the exact value match alone (claim 3) is NOT enough.")
    print("VERIFIED requires the non-LLM authoritative match AND a trusted, content-bound")
    print("binding proof; anything less abstains honestly instead of overclaiming.")


if __name__ == "__main__":
    main()
