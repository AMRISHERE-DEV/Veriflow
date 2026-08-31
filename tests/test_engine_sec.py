"""Public SEC/XBRL assurance helper tests.

The helper is only a convenience wrapper. These tests prove it routes through the
existing engine, fails open when resolution/fetch fails, and never creates a
second status layer.
"""
from __future__ import annotations

import importlib
import unittest

from veriflow.engine import ReleaseDecision, assure_sec_claim, verify_receipt
from veriflow.lanes.financial import issue_trusted_companyfacts
from veriflow.lanes.financial.models import companyfacts_are_trusted, financial_binding_subject
from veriflow.verify.status import EvidenceStatus
from veriflow.verify.trust import issue_trusted_binding_proof

CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
FILED_VALUE = 383285000000.0
TRUSTED_BINDING = object()


def companyfacts(value: float = FILED_VALUE) -> dict:
    # Stands in for an AUTHORITATIVE fetch -> explicitly trusted (a plain dict cannot mint VERIFIED).
    return issue_trusted_companyfacts({
        "cik": 320193,
        "facts": {"us-gaap": {CONCEPT: {"units": {"USD": [
            {"val": value, "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY",
             "form": "10-K", "start": "2022-09-25", "end": "2023-09-30", "frame": "CY2023"},
        ]}}}}
    })


def _call(**overrides):
    base = {
        "claim_id": "c1",
        "text": "Apple FY2023 net sales were $383.285B.",
        "concept": CONCEPT,
        "claimed_value": FILED_VALUE,
        "fiscal_year": 2023,
        "fiscal_period": "FY",
        "ticker": "AAPL",
        "cik": "0000320193",
    }
    base.update(overrides)
    if base.get("binding_proof") is TRUSTED_BINDING:
        resolved_cik = base["cik"] or ("0000320193" if base.get("ticker") == "AAPL" else None)
        subject = financial_binding_subject(
            claim_id=base["claim_id"], text=base["text"], concept=base["concept"],
            claimed_value=base["claimed_value"], fiscal_year=base["fiscal_year"],
            fiscal_period=base.get("fiscal_period", "FY"), unit=base.get("unit", "USD"),
            cik=resolved_cik, ticker=base.get("ticker"), form=base.get("form"),
        )
        base["binding_proof"] = issue_trusted_binding_proof(
            "human", True, "test-stipulated text->concept", issuer="test", subject=subject)
    return assure_sec_claim(**base)


class SecAssuranceHelperTests(unittest.TestCase):
    def test_prefetched_facts_exact_match_allows_with_receipt(self):
        def should_not_fetch(*_args, **_kwargs):
            raise AssertionError("network fetch should not run when companyfacts are supplied")

        r = _call(companyfacts=companyfacts(), fetcher=should_not_fetch, binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertTrue(r.certification.definitive_nonllm)
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW)
        self.assertTrue(verify_receipt(r.receipt))
        self.assertIn("as reported", r.certification.detail)

    def test_prefetched_mismatch_without_binding_does_not_refute(self):
        r = _call(claimed_value=999000000000.0, companyfacts=companyfacts())
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_ticker_only_resolves_and_fetches(self):
        calls = []

        def resolve(ticker, **_kwargs):
            calls.append(("resolve", ticker))
            return "0000320193"

        def fetch(cik, **_kwargs):
            calls.append(("fetch", cik))
            return companyfacts()

        r = _call(cik=None, companyfacts=None, ticker_resolver=resolve, fetcher=fetch, binding_proof=TRUSTED_BINDING)
        self.assertEqual(calls, [("resolve", "AAPL"), ("fetch", "0000320193")])
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertEqual(r.proposed.payload["cik"], "0000320193")

    def test_cik_preferred_over_ticker_resolution(self):
        calls = []

        def resolve(_ticker, **_kwargs):
            raise AssertionError("ticker resolver should not run when cik is supplied")

        def fetch(cik, **_kwargs):
            calls.append(cik)
            return companyfacts()

        r = _call(companyfacts=None, ticker_resolver=resolve, fetcher=fetch, binding_proof=TRUSTED_BINDING)
        self.assertEqual(calls, ["0000320193"])
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)

    def test_fetch_failure_fails_open_to_unverified(self):
        def fetch(_cik, **_kwargs):
            raise RuntimeError("network down")

        r = _call(companyfacts=None, fetcher=fetch)
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertFalse(r.certification.definitive_nonllm)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)
        self.assertTrue(verify_receipt(r.receipt))

    def test_ticker_resolution_failure_fails_open_to_unverified(self):
        def resolve(_ticker, **_kwargs):
            raise RuntimeError("bad mapping")

        r = _call(cik=None, companyfacts=None, ticker_resolver=resolve)
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_no_identity_and_no_source_is_unverified(self):
        r = _call(cik=None, ticker=None, companyfacts=None)
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_live_sec_fetch_is_byte_capped(self):
        resolver = importlib.import_module("veriflow.lanes.financial.resolver")
        requested = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                requested.append(size)
                return b"x" * size

        original = resolver.urllib.request.urlopen
        resolver.urllib.request.urlopen = lambda req, timeout=None: Response()
        try:
            result = resolver.fetch_companyfacts(
                "320193", user_agent="VeriFlow test operator@example.test")
        finally:
            resolver.urllib.request.urlopen = original
        self.assertIsNone(result)
        self.assertEqual(requested, [resolver._MAX_COMPANYFACTS_BYTES + 1])

    def test_realistic_large_companyfacts_document_can_be_trusted_and_detects_mutation(self):
        # Just over the global 100k-node canonical limit, while remaining well
        # inside the SEC adapter's independently enforced byte budget.
        payload = {
            "cik": 320193,
            "facts": {},
            "padding": [{"n": index} for index in range(50_001)],
        }

        trusted = issue_trusted_companyfacts(payload, issuer="edgar-test")

        self.assertTrue(companyfacts_are_trusted(trusted))
        trusted["padding"][0]["n"] = -1
        self.assertFalse(companyfacts_are_trusted(trusted))

    def test_live_sec_fetch_requires_real_operator_identity(self):
        resolver = importlib.import_module("veriflow.lanes.financial.resolver")
        self.assertIsNone(resolver.fetch_companyfacts("320193", user_agent=""))
        self.assertIsNone(resolver.fetch_companyfacts(
            "320193", user_agent="VeriFlow set-your-email@example.com"))

    def test_live_sec_fetch_rejects_invalid_cik_before_network(self):
        resolver = importlib.import_module("veriflow.lanes.financial.resolver")
        original = resolver.urllib.request.urlopen
        resolver.urllib.request.urlopen = lambda *_args, **_kwargs: self.fail("network called")
        try:
            result = resolver.fetch_companyfacts(
                "CIK-1", user_agent="VeriFlow test operator@example.test")
        finally:
            resolver.urllib.request.urlopen = original
        self.assertIsNone(result)

    def test_default_claim_id_is_deterministic(self):
        a = assure_sec_claim(
            text="Apple FY2023 net sales were $383.285B.",
            concept=CONCEPT,
            claimed_value=FILED_VALUE,
            fiscal_year=2023,
            fiscal_period="FY",
            ticker="AAPL",
            companyfacts=companyfacts(),
        )
        b = assure_sec_claim(
            text="Apple FY2023 net sales were $383.285B.",
            concept=CONCEPT,
            claimed_value=FILED_VALUE,
            fiscal_year=2023,
            fiscal_period="FY",
            ticker="AAPL",
            companyfacts=companyfacts(),
        )
        self.assertEqual(a.proposed.claim_id, b.proposed.claim_id)
        self.assertEqual(a.receipt.receipt_hash, b.receipt.receipt_hash)

    def test_output_uses_existing_status_enum(self):
        r = _call(companyfacts=companyfacts())
        self.assertIs(type(r.certification.status), EvidenceStatus)


if __name__ == "__main__":
    unittest.main(verbosity=2)
