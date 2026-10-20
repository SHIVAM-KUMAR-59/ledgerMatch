import threading
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient, APITestCase

from recon.models import BankTransaction, LineItem, Organization, TransactionMatch
from recon.services.csv_import import import_statement
from recon.services.reconciliation import ALL_REJECTED_REASON, IGNORED_REASON

HEADER = "date,description,amount,type,reference\n"

# Northwind rows. Each outcome below is what the matching rules produce at import.
FULL = "2026-04-03,WIRE RAHUL SHAH TRUST REF CC-1002,25000.00,credit,FT-FULL"  # suggested CC-1002
PART = "2026-04-04,ACH HALLER CAPITAL CC-1001,20000.00,credit,FT-PART"  # suggested CC-1001 (partial)
PART2 = "2026-04-05,ACH HALLER CAPITAL CC-1001,20000.00,credit,FT-PART2"  # suggested CC-1001 (partial)
MULTI = "2026-04-11,WIRE OAKRIDGE HOLDINGS LLC,15000.00,credit,FT-MULTI"  # needs_review: CC-1005, CC-1006
UNKNOWN = "2026-04-09,UNKNOWN WIRE,9999.99,credit,FT-UNK"  # needs_review, no matches
FEE = "2026-04-05,BANK FEE,-25.00,debit,FT-FEE"  # ignored debit
# Bluebird row.
CEDAR = "2026-04-16,WIRE CEDAR GROVE FUND CC-2001,40000.00,credit,FT-CEDAR"  # suggested CC-2001


def csv_text(*rows):
    return (HEADER + "".join(row + "\n" for row in rows)).encode("utf-8")


class ActionTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        import_statement(
            Organization.objects.get(slug="northwind"),
            csv_text(FULL, PART, PART2, MULTI, UNKNOWN, FEE),
        )
        import_statement(
            Organization.objects.get(slug="bluebird"), csv_text(CEDAR)
        )

    # lookups
    def txn(self, reference):
        return BankTransaction.objects.get(bank_reference=reference)

    def match(self, reference, item_reference=None):
        qs = TransactionMatch.objects.filter(transaction__bank_reference=reference)
        if item_reference:
            qs = qs.filter(line_item__reference=item_reference)
        return qs.get()

    def item(self, reference):
        return LineItem.objects.get(reference=reference)

    # actions
    def post(self, path, org="northwind"):
        return self.client.post(path, HTTP_X_ORGANIZATION=org)

    def approve(self, match, org="northwind"):
        return self.post(f"/api/matches/{match.pk}/approve/", org)

    def reject(self, match, org="northwind"):
        return self.post(f"/api/matches/{match.pk}/reject/", org)

    def ignore(self, txn, org="northwind"):
        return self.post(f"/api/transactions/{txn.pk}/ignore/", org)

    def assert_nothing_funded(self):
        self.assertFalse(LineItem.objects.exclude(amount_funded=0).exists())


class ApproveTests(ActionTestCase):
    def test_full_payment_funds_the_line_item(self):
        response = self.approve(self.match("FT-FULL"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "reconciled")
        self.assertEqual([m["status"] for m in data["matches"]], ["approved"])

        item = self.item("CC-1002")
        self.assertEqual(item.amount_funded, Decimal("25000.00"))
        self.assertEqual(item.remaining, Decimal("0.00"))
        self.assertEqual(item.status, "funded")
        self.assertEqual(self.txn("FT-FULL").status, "reconciled")

    def test_partial_payment_leaves_the_line_item_partially_funded(self):
        self.approve(self.match("FT-PART"))
        item = self.item("CC-1001")
        self.assertEqual(item.amount_funded, Decimal("20000.00"))
        self.assertEqual(item.remaining, Decimal("7500.00"))
        self.assertEqual(item.status, "partially_funded")

    def test_approving_one_match_rejects_the_others(self):
        chosen = self.match("FT-MULTI", "CC-1005")
        other = self.match("FT-MULTI", "CC-1006")
        self.assertEqual(self.approve(chosen).status_code, 200)

        chosen.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((chosen.status, other.status), ("approved", "rejected"))
        self.assertEqual(self.txn("FT-MULTI").status, "reconciled")
        self.assertEqual(self.item("CC-1005").status, "funded")
        self.assertEqual(self.item("CC-1006").amount_funded, Decimal("0.00"))
        self.assertEqual(self.item("CC-1006").status, "open")

    def test_second_approval_is_409_with_no_double_funding(self):
        match = self.match("FT-FULL")
        self.assertEqual(self.approve(match).status_code, 200)
        self.assertEqual(self.approve(match).status_code, 409)
        self.assertEqual(self.item("CC-1002").amount_funded, Decimal("25000.00"))

    def test_other_match_of_a_reconciled_transaction_is_409(self):
        self.approve(self.match("FT-MULTI", "CC-1005"))
        response = self.approve(self.match("FT-MULTI", "CC-1006"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.item("CC-1006").amount_funded, Decimal("0.00"))

    def test_amount_is_rechecked_at_approval_time(self):
        first = self.match("FT-PART")
        second = self.match("FT-PART2")  # also suggested: remaining was 27,500.00 at import
        self.assertEqual(self.approve(first).status_code, 200)

        response = self.approve(second)
        self.assertEqual(response.status_code, 409)
        self.assertIn("exceeds remaining 7,500.00", response.json()["detail"])

        second.refresh_from_db()
        self.assertEqual(second.status, "proposed")
        self.assertEqual(self.txn("FT-PART2").status, "suggested")
        self.assertEqual(self.item("CC-1001").amount_funded, Decimal("20000.00"))

    def test_cannot_approve_a_rejected_match(self):
        match = self.match("FT-FULL")
        self.reject(match)
        self.assertEqual(self.approve(match).status_code, 409)
        self.assert_nothing_funded()

    def test_cannot_approve_a_match_of_an_ignored_transaction(self):
        self.ignore(self.txn("FT-FULL"))
        self.assertEqual(self.approve(self.match("FT-FULL")).status_code, 409)
        self.assert_nothing_funded()


class RejectTests(ActionTestCase):
    def test_rejecting_the_only_match_moves_to_needs_review(self):
        response = self.reject(self.match("FT-FULL"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "needs_review")
        self.assertEqual(data["reason"], ALL_REJECTED_REASON)
        self.assertEqual([m["status"] for m in data["matches"]], ["rejected"])
        self.assert_nothing_funded()

    def test_rejecting_one_of_two_keeps_the_other_proposed(self):
        original_reason = self.txn("FT-MULTI").reason
        first = self.match("FT-MULTI", "CC-1005")
        second = self.match("FT-MULTI", "CC-1006")

        data = self.reject(first).json()
        self.assertEqual(data["status"], "needs_review")
        self.assertEqual(data["reason"], original_reason)
        second.refresh_from_db()
        self.assertEqual(second.status, "proposed")

    def test_rejecting_the_last_proposed_match_moves_to_needs_review(self):
        self.reject(self.match("FT-MULTI", "CC-1005"))
        data = self.reject(self.match("FT-MULTI", "CC-1006")).json()
        self.assertEqual(data["status"], "needs_review")
        self.assertEqual(data["reason"], ALL_REJECTED_REASON)
        self.assertEqual({m["status"] for m in data["matches"]}, {"rejected"})

    def test_rejecting_twice_is_409(self):
        match = self.match("FT-FULL")
        self.assertEqual(self.reject(match).status_code, 200)
        self.assertEqual(self.reject(match).status_code, 409)

    def test_cannot_reject_a_match_of_a_reconciled_transaction(self):
        match = self.match("FT-FULL")
        self.approve(match)
        self.assertEqual(self.reject(match).status_code, 409)
        match.refresh_from_db()
        self.assertEqual(match.status, "approved")
        self.assertEqual(self.item("CC-1002").amount_funded, Decimal("25000.00"))


class IgnoreTests(ActionTestCase):
    def test_ignoring_a_suggested_transaction_rejects_its_matches(self):
        response = self.ignore(self.txn("FT-FULL"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ignored")
        self.assertEqual(data["reason"], IGNORED_REASON)
        self.assertEqual([m["status"] for m in data["matches"]], ["rejected"])
        self.assert_nothing_funded()

    def test_ignoring_needs_review_with_several_matches(self):
        data = self.ignore(self.txn("FT-MULTI")).json()
        self.assertEqual(data["status"], "ignored")
        self.assertEqual({m["status"] for m in data["matches"]}, {"rejected"})
        self.assertEqual(len(data["matches"]), 2)

    def test_ignoring_needs_review_without_matches(self):
        data = self.ignore(self.txn("FT-UNK")).json()
        self.assertEqual(data["status"], "ignored")
        self.assertEqual(data["matches"], [])

    def test_ignoring_a_reconciled_transaction_is_409(self):
        self.approve(self.match("FT-FULL"))
        response = self.ignore(self.txn("FT-FULL"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.txn("FT-FULL").status, "reconciled")
        self.assertEqual(self.match("FT-FULL").status, "approved")
        self.assertEqual(self.item("CC-1002").amount_funded, Decimal("25000.00"))

    def test_ignoring_twice_is_409(self):
        txn = self.txn("FT-UNK")
        self.assertEqual(self.ignore(txn).status_code, 200)
        self.assertEqual(self.ignore(txn).status_code, 409)

    def test_an_ignored_debit_cannot_be_ignored_again(self):
        self.assertEqual(self.txn("FT-FEE").status, "ignored")
        self.assertEqual(self.ignore(self.txn("FT-FEE")).status_code, 409)


class OrgIsolationTests(ActionTestCase):
    def test_another_orgs_records_are_404_for_every_action(self):
        cedar_match = self.match("FT-CEDAR")
        cedar_txn = self.txn("FT-CEDAR")
        paths = [
            f"/api/matches/{cedar_match.pk}/approve/",
            f"/api/matches/{cedar_match.pk}/reject/",
            f"/api/transactions/{cedar_txn.pk}/ignore/",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.post(path, "northwind").status_code, 404)

        cedar_match.refresh_from_db()
        cedar_txn.refresh_from_db()
        self.assertEqual(cedar_match.status, "proposed")
        self.assertEqual(cedar_txn.status, "suggested")
        self.assertEqual(self.item("CC-2001").amount_funded, Decimal("0.00"))

    def test_the_owning_org_can_act(self):
        response = self.approve(self.match("FT-CEDAR"), org="bluebird")
        self.assertEqual(response.status_code, 200)
        item = self.item("CC-2001")
        self.assertEqual(item.amount_funded, Decimal("40000.00"))
        self.assertEqual(item.status, "funded")

    def test_unknown_ids_are_404(self):
        for path in (
            "/api/matches/999999/approve/",
            "/api/matches/999999/reject/",
            "/api/transactions/999999/ignore/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.post(path).status_code, 404)

    def test_actions_need_the_org_header(self):
        response = self.client.post(f"/api/matches/{self.match('FT-FULL').pk}/approve/")
        self.assertEqual(response.status_code, 400)
        self.assert_nothing_funded()


class ConcurrentApprovalTests(TransactionTestCase):
    """Real threads against real committed data (TransactionTestCase, not TestCase)."""

    def setUp(self):
        call_command("seed_demo", stdout=StringIO())
        import_statement(
            Organization.objects.get(slug="northwind"), csv_text(FULL, PART, PART2)
        )

    def post_simultaneously(self, paths):
        barrier = threading.Barrier(len(paths))
        statuses = []

        def worker(path):
            try:
                client = APIClient()
                barrier.wait(timeout=10)
                response = client.post(path, HTTP_X_ORGANIZATION="northwind")
                statuses.append(response.status_code)
            finally:
                connection.close()  # each thread opens its own DB connection

        threads = [threading.Thread(target=worker, args=(p,)) for p in paths]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return sorted(statuses)

    def test_two_identical_approvals_fund_the_line_item_once(self):
        match = TransactionMatch.objects.get(transaction__bank_reference="FT-FULL")
        path = f"/api/matches/{match.pk}/approve/"

        self.assertEqual(self.post_simultaneously([path, path]), [200, 409])

        item = LineItem.objects.get(reference="CC-1002")
        self.assertEqual(item.amount_funded, Decimal("25000.00"))
        self.assertEqual(item.status, "funded")
        approved = TransactionMatch.objects.filter(
            transaction__bank_reference="FT-FULL", status="approved"
        )
        self.assertEqual(approved.count(), 1)

    def test_two_transactions_racing_for_one_line_item(self):
        # FT-PART and FT-PART2 are both 20,000.00 against CC-1001 (27,500.00 called).
        matches = TransactionMatch.objects.filter(line_item__reference="CC-1001")
        paths = [f"/api/matches/{m.pk}/approve/" for m in matches]
        self.assertEqual(len(paths), 2)

        self.assertEqual(self.post_simultaneously(paths), [200, 409])

        item = LineItem.objects.get(reference="CC-1001")
        self.assertEqual(item.amount_funded, Decimal("20000.00"))
        self.assertEqual(item.status, "partially_funded")
        statuses = sorted(
            BankTransaction.objects.filter(
                bank_reference__in=["FT-PART", "FT-PART2"]
            ).values_list("status", flat=True)
        )
        self.assertEqual(statuses, ["reconciled", "suggested"])