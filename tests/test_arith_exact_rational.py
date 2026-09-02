"""Class 6 pins: the arithmetic lane must be exactly rational END TO END.

Found during kernel-benchmark design (2026-09-03): integer literals stayed
``int`` while decimal literals became ``Fraction``, so ``1 / 10`` evaluated to
the float 0.1 and was compared against ``Fraction(1, 10)``. Result on shipped
v1.1.1: ``1 / 10 = 0.1`` REFUTED (false), ``1 / 10 > 0.1`` VERIFIED (false) -
a wrong-direction definitive verdict on the README quickstart path.

Policy: every literal is an exact rational; division is exact; a non-integer
exponent is not exactly computable and must ABSTAIN (never REFUTE, never
VERIFY on float luck).
"""
import unittest
from datetime import datetime, timezone

from glass_ionomer.verify.pipeline import verify_text
from glass_ionomer.verify.verifiers import ArithmeticVerifier

NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def status_of(text: str) -> str:
    out = verify_text(text, [], [ArithmeticVerifier()], now=NOW)
    return getattr(out.decision.status, "value", out.decision.status)


class ExactRationalArithmeticTests(unittest.TestCase):

    def test_integer_division_equals_decimal_literal(self):
        for t in ("1 / 10 = 0.1", "12 / 5 = 2.4", "1 / 4 = 0.25", "3 / 10 = 0.3", "1 / 3 * 3 = 1"):
            self.assertEqual(status_of(t), "verified", t)

    def test_integer_division_false_relations_are_refuted(self):
        for t in ("1 / 10 > 0.1", "3 / 10 < 0.3", "1 / 10 = 0.2", "12 / 5 = 2.5"):
            self.assertEqual(status_of(t), "refuted", t)

    def test_non_integer_exponent_abstains(self):
        # Neither REFUTED (v1.1.1 behavior on true claims) nor VERIFIED by float luck.
        for t in ("(2 ** 0.5) ** 2 = 2", "9 ** 0.5 = 3", "2 ** 0.5 * 2 ** 0.5 = 2", "9 ** 0.5 = 4"):
            self.assertEqual(status_of(t), "unverified", t)

    def test_integer_valued_exponents_stay_exact(self):
        self.assertEqual(status_of("2 ** 10 = 1024"), "verified")
        self.assertEqual(status_of("2 ** 10 = 1000"), "refuted")
        self.assertEqual(status_of("0.5 ** 2 = 0.25"), "verified")

    def test_decimal_identities_and_controls_unchanged(self):
        self.assertEqual(status_of("0.1 + 0.2 = 0.3"), "verified")
        self.assertEqual(status_of("2 + 2 = 4"), "verified")
        self.assertEqual(status_of("2 + 2 = 5"), "refuted")
        self.assertEqual(status_of("7 // 2 = 3"), "verified")
        self.assertEqual(status_of("-7 % 3 = 2"), "verified")


if __name__ == "__main__":
    unittest.main()
