"""Contract tests for the financial (SEC/XBRL) verification lane.

These prove the lane reaches VERIFIED only via an authoritative deterministic
match routed through the hardened kernel, and that the dangerous cases
(scale error, fabricated tag, restatement, prose claim) can NEVER reach VERIFIED.
"""
import copy
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from glass_ionomer.lanes.financial import (
    FinancialClaim,
    SecFact,
    issue_trusted_companyfacts,
    parse_money,
    select_period,
    verify_financial_claim,
    verify_prose_claim,
)
from glass_ionomer.verify.trust import issue_trusted_binding_proof

NOW = datetime(2026, 6, 27, tzinfo=timezone.utc)

TRUSTED_BINDING = object()

# Apple-like EDGAR companyfacts fixture (data.sec.gov shape). "Restated" is a
# synthetic concept that carries two distinct values for the same period.
COMPANYFACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {"USD": [
                    {"start": "2022-09-25", "end": "2023-09-30", "val": 383285000000,
                     "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY", "form": "10-K", "frame": "CY2023"},
                    {"start": "2021-09-26", "end": "2022-09-24", "val": 394328000000,
                     "accn": "0000320193-22-000108", "fy": 2022, "fp": "FY", "form": "10-K", "frame": "CY2022"},
                ]}
            },
            "NetIncomeLoss": {
                "units": {"USD": [
                    {"start": "2022-09-25", "end": "2023-09-30", "val": 96995000000,
                     "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY", "form": "10-K"},
                ]}
            },
            "RestatedThing": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 100.0, "accn": "0000320193-23-000106",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2023-09-30", "val": 110.0, "accn": "0000320193-24-000050",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                ]}
            },
            # near-synonym concept holding a DIFFERENT value (concept-alias red-team)
            "SalesRevenueNet": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 380000000000, "accn": "0000320193-23-000106",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                ]}
            },
            # both FY and Q1 entries (period-routing red-team)
            "RevMulti": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 200, "accn": "0000320193-23-000106",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2022-12-31", "val": 50, "accn": "0000320193-23-000060",
                     "fy": 2023, "fp": "Q1", "form": "10-Q"},
                ]}
            },
            # restatement where the amendment is a different FORM (form-pin red-team)
            "FormRestated": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 100, "accn": "0000000000-23-000001",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2023-09-30", "val": 130, "accn": "0000000000-24-000001",
                     "fy": 2023, "fp": "FY", "form": "10-K/A"},
                ]}
            },
            # restatement where the amendment row lacks fy/fp tags (end-date bucketing red-team)
            "UntaggedAmend": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 100, "accn": "0000000000-23-000002",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2023-09-30", "val": 130, "accn": "0000000000-24-000002",
                     "fy": None, "fp": None, "form": "10-K/A"},
                ]}
            },
            # cents-level restatement (per-share) - a real 7-cent restatement
            "EpsRestate": {
                "units": {"USD/shares": [
                    {"end": "2023-09-30", "val": 6.13, "accn": "0000000000-23-000003",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2023-09-30", "val": 6.20, "accn": "0000000000-24-000003",
                     "fy": 2023, "fp": "FY", "form": "10-K/A"},
                ]}
            },
            # non-integer (cents) figure - the relative-tolerance red-team target
            "GrossProfitCents": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 1000000000.50, "accn": "0000000000-23-000007",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                ]}
            },
            # large-magnitude non-integer restatement ($7,200 apart) - relative-dedup collapse red-team
            "BigNonIntRestate": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 9000000000000.50, "accn": "0000000000-23-000008",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2023-09-30", "val": 9000000007200.50, "accn": "0000000000-24-000008",
                     "fy": 2023, "fp": "FY", "form": "10-K/A"},
                ]}
            },
            # amendment row with NO end (and no fy/fp) - end-bucketing-evasion red-team
            "UntaggedNoEnd": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 100, "accn": "0000000000-23-000009",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"val": 130, "accn": "0000000000-24-000009", "fy": None, "fp": None, "form": "10-K/A"},
                ]}
            },
            # clean amendment: SAME value across two filings -> verify, bind LATEST accession
            "CleanAmend": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 130, "accn": "0000000000-23-000004",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2023-09-30", "val": 130, "accn": "0000000000-24-000004",
                     "fy": 2023, "fp": "FY", "form": "10-K/A"},
                ]}
            },
            # small negative per-share figure (sign-inversion red-team, round 3)
            "EpsAdjustment": {
                "units": {"USD/shares": [
                    {"end": "2023-09-30", "val": -0.0025, "accn": "0000000000-23-000011",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                ]}
            },
            # per-share figure where a half-cent is MATERIAL (unit-blind tolerance red-team)
            "EpsDiluted": {
                "units": {"USD/shares": [
                    {"end": "2023-09-30", "val": 6.13, "accn": "0000000000-23-000012",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                ]}
            },
            # sub-half-cent restatement (dedup-collapse red-team)
            "SubcentRestate": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 100.00, "accn": "0000000000-23-000013",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2023-09-30", "val": 100.004, "accn": "0000000000-24-000013",
                     "fy": 2023, "fp": "FY", "form": "10-K/A"},
                ]}
            },
            # restatement whose amendment carries NON-CANONICAL tags ("FY ", different end)
            "NonCanonTag": {
                "units": {"USD": [
                    {"end": "2023-09-30", "val": 100, "accn": "0000000000-23-000014",
                     "fy": 2023, "fp": "FY", "form": "10-K"},
                    {"end": "2024-02-02", "val": 999, "accn": "0000000000-24-000014",
                     "fy": 2023, "fp": "FY ", "form": "10-K/A"},
                ]}
            },
        }
    },
}

# The fixture stands in for an AUTHORITATIVE EDGAR fetch, so it is explicitly trusted (a plain dict
# is caller-supplied/forgeable and cannot mint VERIFIED - see test_forged_companyfacts_*).
COMPANYFACTS = issue_trusted_companyfacts(COMPANYFACTS)

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def _bind(claim: FinancialClaim) -> FinancialClaim:
    proof = issue_trusted_binding_proof(
        "human", True, "test-stipulated text->concept", issuer="test",
        subject=claim.binding_subject(),
    )
    return replace(claim, binding_proof=proof)


def _claim(value, concept=REV, fy=2023, fp="FY", unit="USD",
           binding_proof=TRUSTED_BINDING):
    claim = FinancialClaim(
        claim_id="C1", text="Apple FY2023 revenue claim", concept=concept,
        claimed_value=value, fiscal_year=fy, fiscal_period=fp, unit=unit,
    )
    if binding_proof is TRUSTED_BINDING:
        return _bind(claim)
    return replace(claim, binding_proof=binding_proof)


class FinancialLaneTests(unittest.TestCase):

    def test_compatibility_period_selector_normalizes_labels_without_mixing_periods(self):
        facts = [
            SecFact(
                concept="Revenue", value=10.0, unit="USD", fiscal_year=2023,
                fiscal_period="FY", form="10-K", start=None, end="2023-12-31",
                frame=None, accession="annual",
            ),
            SecFact(
                concept="Revenue", value=3.0, unit="USD", fiscal_year=2023,
                fiscal_period="Q4", form="10-Q", start=None, end="2023-12-31",
                frame=None, accession="quarter",
            ),
        ]
        selected = select_period(facts, " 2023 ", " fy ", " 10-k ")
        self.assertEqual([f.accession for f in selected], ["annual"])

    def test_exact_match_is_verified_with_provenance(self):
        pack = verify_financial_claim(_claim(383285000000), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "verified")
        self.assertEqual(pack.tier.value, "L3_deterministic")
        self.assertEqual(pack.lane, "A")
        self.assertEqual(pack.as_reported_in, "0000320193-23-000106")  # the accession is the proof
        self.assertIn("as reported", pack.kernel_wording)

    def test_comparative_rows_in_one_filing_are_not_false_restatements(self):
        payload = {
            "cik": 320193,
            "facts": {"us-gaap": {REV: {"units": {"USD": [
                {"start": "2020-09-27", "end": "2021-09-25", "val": 365817000000,
                 "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY", "form": "10-K"},
                {"start": "2021-09-26", "end": "2022-09-24", "val": 394328000000,
                 "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY", "form": "10-K"},
                {"start": "2022-09-25", "end": "2023-09-30", "val": 383285000000,
                 "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY", "form": "10-K"},
            ]}}}},
        }
        fixture = issue_trusted_companyfacts(payload, issuer="edgar-test")

        pack = verify_financial_claim(_claim(383285000000), fixture, now=NOW)

        self.assertEqual(pack.status, "verified")
        self.assertEqual(pack.as_reported_in, "0000320193-23-000106")

    def test_forged_companyfacts_dict_cannot_verify(self):
        # P0 regression (#2): a caller-supplied PLAIN dict (forgeable) is not authoritative -> never VERIFIED,
        # even on an exact value match. Only a trusted (fetched/audited) companyfacts can.
        forged = dict(COMPANYFACTS)  # unwrap -> plain dict, indistinguishable from an attacker's
        pack = verify_financial_claim(_claim(383285000000), forged, now=NOW)
        self.assertNotEqual(pack.status, "verified")

    def test_untrusted_text_concept_binding_caps_below_verified(self):
        # P1 regression (#4): an exact tuple match with NO trusted text->concept proof must not reach
        # top-level VERIFIED (kills the "net income"=GrossProfit-value overclaim).
        pack = verify_financial_claim(_claim(383285000000, binding_proof=None), COMPANYFACTS, now=NOW)
        self.assertNotEqual(pack.status, "verified")

    def test_money_string_parses_to_base_units(self):
        self.assertAlmostEqual(parse_money("$383.3B"), 383.3e9)
        self.assertAlmostEqual(parse_money("383.3 billion"), 383.3e9)
        self.assertAlmostEqual(parse_money("383,285,000,000"), 383285000000.0)
        self.assertIsNone(parse_money("no number here"))
        self.assertAlmostEqual(parse_money("In FY2023, revenue was $383.3B"), 383.3e9)
        self.assertIsNone(parse_money("In FY2023, revenue was 383285000000"))
        self.assertIsNone(parse_money("Revenue was $10B, up from $9B"))

    def test_exact_money_string_verifies(self):
        # "$383.285B" is exactly the filed figure (round-off tolerance only).
        pack = verify_financial_claim(_claim(parse_money("$383.285B")), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "verified")

    def test_rounded_approximation_is_not_verified(self):
        # "$383.3B" rounds $383.285B -> a DIFFERENT number ($15M off) -> must NOT verify (red-team: tolerance).
        pack = verify_financial_claim(_claim(parse_money("$383.3B")), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "refuted")

    def test_mismatch_is_refuted(self):
        pack = verify_financial_claim(_claim(999000000000), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "refuted")
        self.assertNotEqual(pack.status, "verified")

    # --- MOAT TESTS: dangerous cases must NEVER reach VERIFIED --------------- #
    def test_scale_error_is_not_verified(self):
        # Someone forgot the scale: 383,285 (thousands) vs filed 383,285,000,000.
        pack = verify_financial_claim(_claim(383285), COMPANYFACTS, now=NOW)
        self.assertNotEqual(pack.status, "verified")
        self.assertEqual(pack.status, "refuted")

    def test_fabricated_concept_is_unverified(self):
        pack = verify_financial_claim(_claim(1.0, concept="TotallyMadeUpConcept"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "unverified")
        self.assertIsNone(pack.matched_fact)

    def test_wrong_period_is_unverified(self):
        pack = verify_financial_claim(_claim(383285000000, fy=2019), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "unverified")

    def test_restatement_is_contested_not_verified(self):
        pack = verify_financial_claim(_claim(100.0, concept="RestatedThing"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")
        self.assertNotEqual(pack.status, "verified")
        self.assertIsNone(pack.as_reported_in)  # ambiguous which filing -> no single proof

    def test_near_but_wrong_value_is_refuted(self):
        # A value 0.45% off (would have passed the old 1% band) must refute under exact matching.
        pack = verify_financial_claim(_claim(385000000000), COMPANYFACTS, now=NOW)  # vs 383285000000
        self.assertEqual(pack.status, "refuted")

    def test_non_finite_claim_is_unverified(self):
        for bad in (float("inf"), float("nan"), float("-inf")):
            pack = verify_financial_claim(_claim(bad), COMPANYFACTS, now=NOW)
            self.assertEqual(pack.status, "unverified")

    def test_non_finite_filed_values_are_ignored(self):
        malformed = copy.deepcopy(dict(COMPANYFACTS))
        malformed["facts"]["us-gaap"]["NonFiniteFact"] = {"units": {"USD": [{
            "val": float("nan"), "accn": "bad", "fy": 2023, "fp": "FY", "form": "10-K",
        }]}}
        pack = verify_financial_claim(
            _claim(1.0, concept="NonFiniteFact"), malformed, now=NOW)
        self.assertEqual(pack.status, "unverified")
        self.assertIsNone(pack.matched_fact)

    def test_prose_claim_is_capped_never_verified(self):
        pack = verify_prose_claim("C2", "Apple's pricing power is unmatched in the industry.")
        self.assertNotEqual(pack.status, "verified")
        self.assertEqual(pack.lane, "B")
        self.assertEqual(pack.tier.value, "L1_quote_located")

    # --- replayability ------------------------------------------------------ #
    def test_manifest_is_deterministic_and_input_sensitive(self):
        p1 = verify_financial_claim(_claim(383285000000), COMPANYFACTS, now=NOW)
        p2 = verify_financial_claim(_claim(383285000000), COMPANYFACTS, now=NOW)
        p3 = verify_financial_claim(_claim(999000000000), COMPANYFACTS, now=NOW)
        self.assertEqual(p1.manifest_hash, p2.manifest_hash)   # same inputs -> same hash (replayable)
        self.assertNotEqual(p1.manifest_hash, p3.manifest_hash)  # different claim -> different hash

    def test_correct_concept_correct_value_other_metric(self):
        pack = verify_financial_claim(_claim(96995000000, concept="NetIncomeLoss"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "verified")


    # --- RED-TEAM REGRESSIONS: each exploit found by the adversarial workflow ---- #
    def test_period_must_be_explicit_no_wildcard_year(self):
        # period red-team: fiscal_year=None used to wildcard-match ANY year.
        c = FinancialClaim(claim_id="C1", text="revenue $5000", concept=REV,
                           claimed_value=383285000000, fiscal_year=None, fiscal_period="FY")
        self.assertEqual(verify_financial_claim(c, COMPANYFACTS, now=NOW).status, "unverified")

    def test_none_fiscal_period_is_unverified(self):
        c = FinancialClaim(claim_id="C1", text="annual revenue", concept="RevMulti",
                           claimed_value=50, fiscal_year=2023, fiscal_period=None)
        self.assertEqual(verify_financial_claim(c, COMPANYFACTS, now=NOW).status, "unverified")

    def test_quarterly_value_does_not_satisfy_annual(self):
        # FY claim with the Q1 value must not verify; the Q1 claim with the Q1 value must.
        self.assertEqual(verify_financial_claim(_claim(50, concept="RevMulti", fp="FY"), COMPANYFACTS, now=NOW).status, "refuted")
        self.assertEqual(verify_financial_claim(_claim(50, concept="RevMulti", fp="Q1"), COMPANYFACTS, now=NOW).status, "verified")
        self.assertEqual(verify_financial_claim(_claim(200, concept="RevMulti", fp="FY"), COMPANYFACTS, now=NOW).status, "verified")

    def test_same_end_quarter_does_not_create_false_annual_restatement(self):
        fixture = copy.deepcopy(dict(COMPANYFACTS))
        fixture["facts"]["us-gaap"]["RevMulti"]["units"]["USD"].append({
            "end": "2023-09-30", "val": 50, "accn": "0000320193-23-000099",
            "fy": 2023, "fp": "Q4", "form": "10-Q",
        })
        fixture = issue_trusted_companyfacts(fixture)
        self.assertEqual(
            verify_financial_claim(_claim(200, concept="RevMulti", fp="FY"), fixture, now=NOW).status,
            "verified",
        )

    def test_off_by_one_dollar_is_refuted(self):
        # exact-integer match: a $1 difference on a $383B figure must refute (red-team: tolerance/float).
        self.assertEqual(verify_financial_claim(_claim(383285000001), COMPANYFACTS, now=NOW).status, "refuted")

    def test_value_too_large_for_exact_compare_is_unverified(self):
        pack = verify_financial_claim(_claim(float(2 ** 53 + 1)), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "unverified")

    def test_verified_pack_names_the_concept(self):
        # concept-alias red-team: a consumer must see WHICH us-gaap tag was verified.
        pack = verify_financial_claim(_claim(380000000000, concept="SalesRevenueNet"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "verified")
        self.assertIn("SalesRevenueNet", pack.kernel_wording)

    def test_form_pin_cannot_hide_restatement(self):
        # restatement red-team: pinning form="10-K" must NOT prune the 10-K/A amendment.
        c = FinancialClaim(claim_id="C1", text="FY2023 was $100", concept="FormRestated",
                           claimed_value=100, fiscal_year=2023, fiscal_period="FY", form="10-K")
        pack = verify_financial_claim(c, COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")
        self.assertIsNone(pack.as_reported_in)

    def test_untagged_amendment_collides_by_end_date(self):
        # restatement red-team: an amendment row lacking fy/fp must still collide via end-date.
        pack = verify_financial_claim(_claim(100, concept="UntaggedAmend"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")

    def test_small_magnitude_restatement_is_contested(self):
        # pack-integrity red-team: round(,6) used to collapse tiny EPS restatements.
        pack = verify_financial_claim(_claim(6.13, concept="EpsRestate", unit="USD/shares"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")

    def test_clean_amendment_binds_latest_accession(self):
        # same value across original + amendment -> verify, but provenance = the LATEST filing.
        pack = verify_financial_claim(_claim(130, concept="CleanAmend"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "verified")
        self.assertEqual(pack.as_reported_in, "0000000000-24-000004")

    def test_manifest_is_sensitive_to_claim_identity(self):
        a = _bind(FinancialClaim(claim_id="A", text="Apple FY23 revenue", concept=REV,
                                claimed_value=383285000000, fiscal_year=2023, fiscal_period="FY"))
        b = _bind(FinancialClaim(claim_id="B", text="totally different claim", concept=REV,
                                claimed_value=383285000000, fiscal_year=2023, fiscal_period="FY"))
        pa = verify_financial_claim(a, COMPANYFACTS, now=NOW)
        pb = verify_financial_claim(b, COMPANYFACTS, now=NOW)
        self.assertEqual(pa.status, "verified")
        self.assertNotEqual(pa.manifest_hash, pb.manifest_hash)  # claim identity in the manifest

    # --- RED-TEAM ROUND 2: the non-integer (cents) relative-tolerance class ------ #
    def test_non_integer_exact_value_verifies(self):
        pack = verify_financial_claim(_claim(1000000000.50, concept="GrossProfitCents"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "verified")

    def test_non_integer_off_by_one_dollar_is_refuted(self):
        # cents-valued figure, claim off by $1 -> must refute (no magnitude-scaled relative slack).
        pack = verify_financial_claim(_claim(1000000001.50, concept="GrossProfitCents"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "refuted")

    def test_large_non_integer_restatement_is_contested(self):
        # $7,200-apart restatement at ~$9T magnitude must NOT collapse under tolerance -> CONTESTED.
        pack = verify_financial_claim(_claim(9000000000000.50, concept="BigNonIntRestate"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")

    def test_untagged_amendment_without_end_is_contested(self):
        # an amendment row missing BOTH fy/fp AND end must still be caught (fail-closed bucketing).
        pack = verify_financial_claim(_claim(100, concept="UntaggedNoEnd"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")

    def test_verified_wording_names_the_unit(self):
        # a consumer showing only kernel_wording must see the unit (no currency confusion).
        pack = verify_financial_claim(_claim(383285000000), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "verified")
        self.assertIn("USD", pack.kernel_wording)
        self.assertIn("FY2023", pack.kernel_wording)  # period from the claim, never "NoneNone"

    # --- RED-TEAM ROUND 3: exact-equality / sign / unit / non-canonical-tag class - #
    def test_sign_inversion_is_refuted(self):
        # +0.0025 vs filed -0.0025 (a 100% sign error) must refute, not slip under any tolerance.
        pack = verify_financial_claim(_claim(0.0025, concept="EpsAdjustment", unit="USD/shares"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "refuted")

    def test_material_per_share_difference_is_refuted(self):
        # EPS 6.134 vs filed 6.13 is material for a per-share unit -> refuted (no unit-blind half-cent slack).
        self.assertEqual(verify_financial_claim(_claim(6.134, concept="EpsDiluted", unit="USD/shares"), COMPANYFACTS, now=NOW).status, "refuted")
        self.assertEqual(verify_financial_claim(_claim(6.13, concept="EpsDiluted", unit="USD/shares"), COMPANYFACTS, now=NOW).status, "verified")

    def test_subcent_restatement_is_contested(self):
        # 100.00 vs 100.004 are distinct filings -> CONTESTED (restatement dedup is exact, not the match bar).
        pack = verify_financial_claim(_claim(100.00, concept="SubcentRestate"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")
        self.assertIsNone(pack.as_reported_in)

    def test_noncanonical_tag_restatement_is_contested(self):
        # an amendment tagged "FY " (trailing space) must still place into the period -> CONTESTED.
        pack = verify_financial_claim(_claim(100, concept="NonCanonTag"), COMPANYFACTS, now=NOW)
        self.assertEqual(pack.status, "contested")

    def test_manifest_is_sensitive_to_entity(self):
        a = _bind(FinancialClaim(claim_id="C", text="t", concept=REV, claimed_value=383285000000,
                                fiscal_year=2023, fiscal_period="FY", cik="320193", ticker="AAPL"))
        b = _bind(FinancialClaim(claim_id="C", text="t", concept=REV, claimed_value=383285000000,
                                fiscal_year=2023, fiscal_period="FY", cik="789019", ticker="MSFT"))
        pa = verify_financial_claim(a, COMPANYFACTS, now=NOW)
        pb = verify_financial_claim(b, COMPANYFACTS, now=NOW)
        self.assertEqual(pa.status, "verified")
        self.assertNotEqual(pa.manifest_hash, pb.manifest_hash)  # entity identity in the manifest

    def test_binding_proof_cannot_be_replayed_onto_another_financial_claim(self):
        original = _bind(FinancialClaim(
            claim_id="original", text="Apple FY2023 revenue", concept=REV,
            claimed_value=383285000000, fiscal_year=2023, fiscal_period="FY",
        ))
        replayed = FinancialClaim(
            claim_id="replayed", text="Different unreviewed assertion", concept=REV,
            claimed_value=383285000000, fiscal_year=2023, fiscal_period="FY",
            binding_proof=original.binding_proof,
        )
        self.assertEqual(verify_financial_claim(original, COMPANYFACTS, now=NOW).status, "verified")
        self.assertNotEqual(verify_financial_claim(replayed, COMPANYFACTS, now=NOW).status, "verified")


if __name__ == "__main__":
    unittest.main()
