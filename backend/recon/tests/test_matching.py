from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from recon.services.matching import (
    NEEDS_REVIEW,
    SUGGESTED,
    Candidate,
    contains_words,
    decide,
    normalise_name,
    normalise_reference,
    normalise_text,
)

CALL_1 = date(2026, 3, 25)
CALL_2 = date(2026, 4, 1)


def cand(id, reference, name, remaining, call_date):
    return Candidate.build(
        id=id,
        reference=reference,
        investor_name=name,
        remaining=Decimal(remaining),
        call_date=call_date,
    )


NORTHWIND = [
    cand(1, "CC-1001", "Haller Capital LLC", "27500.00", CALL_1),
    cand(2, "CC-1002", "Rahul Shah Trust", "25000.00", CALL_1),
    cand(3, "CC-1003", "Patel Family Trust", "12500.00", CALL_1),
    cand(4, "CC-1004", "Meridian Partners LP", "15000.00", CALL_1),
    cand(5, "CC-1005", "Oakridge Holdings LLC", "15000.00", CALL_1),
    cand(6, "CC-1006", "Oakridge Holdings LLC", "15000.00", CALL_2),
]


def run(description, amount, txn_date, bank_reference="FT0001", candidates=NORTHWIND):
    return decide(
        txn_date=txn_date,
        amount=Decimal(amount),
        description=description,
        bank_reference=bank_reference,
        candidates=candidates,
    )


def ids(decision):
    return [m.line_item_id for m in decision.matches]


class NormalisationTests(SimpleTestCase):
    def test_reference_keeps_only_letters_and_digits(self):
        self.assertEqual(normalise_reference("cc-1003"), "CC1003")
        self.assertEqual(normalise_reference(" cc 1001 "), "CC1001")

    def test_name_drops_one_trailing_suffix(self):
        self.assertEqual(normalise_name("Meridian Partners LP"), "MERIDIAN PARTNERS")
        self.assertEqual(normalise_name("Haller Capital LLC"), "HALLER CAPITAL")
        self.assertEqual(normalise_name("Acme, Inc."), "ACME")

    def test_name_keeps_trust(self):
        self.assertEqual(normalise_name("Rahul Shah Trust"), "RAHUL SHAH TRUST")

    def test_name_that_is_only_a_suffix_normalises_to_empty(self):
        self.assertEqual(normalise_name("LLC"), "")

    def test_names_match_whole_words_only(self):
        self.assertTrue(contains_words(normalise_text("wire meridian partners"), "MERIDIAN PARTNERS"))
        self.assertFalse(
            contains_words(normalise_text("wire meridian partnership"), "MERIDIAN PARTNERS")
        )
        self.assertFalse(contains_words("ANYTHING", ""))


class ReferenceMatchTests(SimpleTestCase):
    def test_reference_full_payment(self):
        d = run("WIRE INCOMING RAHUL SHAH TRUST REF CC-1002", "25000.00", date(2026, 4, 3))
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [2])
        self.assertIn("CC-1002", d.matches[0].reason)
        self.assertIn("amount equals remaining 25,000.00", d.matches[0].reason)

    def test_lowercase_reference_partial_payment(self):
        d = run("ACH CREDIT HALLER CAPITAL cc1001", "7500.00", date(2026, 4, 14))
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [1])
        self.assertIn("partial payment 7,500.00 of remaining 27,500.00", d.matches[0].reason)

    def test_reference_found_in_bank_reference(self):
        d = run("UNKNOWN WIRE", "12500.00", date(2026, 4, 7), bank_reference="CC-1003-FT")
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [3])

    def test_reference_takes_precedence_over_name_and_amount(self):
        # Name+amount alone would match CC-1003, but the reference says CC-1002.
        d = run("PATEL FAMILY TRUST CC-1002", "12500.00", date(2026, 4, 7))
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [2])

    def test_overpayment_is_needs_review_with_no_match(self):
        d = run("WIRE INCOMING PATEL FAMILY TRUST CC-1003", "13000.00", date(2026, 4, 15))
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [])
        self.assertIn("overpayment", d.reason)

    def test_reference_overpayment_does_not_fall_through_to_name_step(self):
        items = [
            cand(1, "CC-9001", "Alpha Fund", "100.00", CALL_1),
            cand(2, "CC-9002", "Beta Fund", "500.00", CALL_1),
        ]
        # Step 3 would match Beta Fund (name + amount), but a reference was found.
        d = run("ALPHA FUND BETA FUND CC-9001", "500.00", date(2026, 4, 3), candidates=items)
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [])

    def test_several_references_propose_all(self):
        d = run("CC-1001 AND CC-1002", "1000.00", date(2026, 4, 3))
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [1, 2])

    def test_reference_to_line_item_not_yet_called_is_ineligible(self):
        d = run("WIRE INCOMING OAKRIDGE HOLDINGS LLC REF CC-1006", "15000.00", date(2026, 3, 20))
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [])
        self.assertIn("No reference found", d.reason)

    def test_call_date_equal_to_transaction_date_is_eligible(self):
        d = run("WIRE REF CC-1006", "15000.00", date(2026, 4, 1))
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [6])

    def test_fully_funded_line_item_is_ineligible(self):
        items = [cand(1, "CC-1", "Alpha Fund", "0.00", CALL_1)]
        d = run("PAYMENT CC-1", "50.00", date(2026, 4, 3), candidates=items)
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [])


class NameMatchTests(SimpleTestCase):
    def test_name_and_amount_match(self):
        d = run("WIRE INCOMING PATEL FAMILY TRUST", "12500.00", date(2026, 4, 7))
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [3])
        self.assertIn("Investor name Patel Family Trust found", d.matches[0].reason)

    def test_lowercase_padded_description_matches(self):
        d = run("  wire incoming meridian partners  ", "15000.00", date(2026, 4, 8))
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [4])

    def test_multiple_candidates_propose_all(self):
        d = run("WIRE INCOMING OAKRIDGE HOLDINGS LLC", "15000.00", date(2026, 4, 11))
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [5, 6])

    def test_call_date_can_narrow_multiple_candidates_to_one(self):
        # CC-1006 is not called until 04-01, so only CC-1005 is eligible on 03-28.
        d = run("WIRE INCOMING OAKRIDGE HOLDINGS LLC", "15000.00", date(2026, 3, 28))
        self.assertEqual(d.status, SUGGESTED)
        self.assertEqual(ids(d), [5])

    def test_name_found_but_amount_differs(self):
        d = run("WIRE INCOMING PATEL FAMILY TRUST", "12000.00", date(2026, 4, 7))
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [])

    def test_partial_word_does_not_count_as_name(self):
        d = run("WIRE INCOMING MERIDIAN PARTNERSHIP", "15000.00", date(2026, 4, 8))
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [])

    def test_unknown_wire_is_needs_review_with_reason(self):
        d = run("UNKNOWN INCOMING WIRE", "9999.99", date(2026, 4, 9))
        self.assertEqual(d.status, NEEDS_REVIEW)
        self.assertEqual(ids(d), [])
        self.assertTrue(d.reason)