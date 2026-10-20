from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from recon.models import BankTransaction, LineItem, TransactionMatch

URL = "/api/transactions/import/"
SAMPLE = Path(__file__).parent / "data" / "sample_bank_statement.csv"
HEADER = "date,description,amount,type,reference\n"


def csv_rows(*rows):
    return HEADER + "".join(row + "\n" for row in rows)


class ImportAPITestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())

    def upload(self, content, org="northwind", field="file"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        upload = SimpleUploadedFile("statement.csv", content, content_type="text/csv")
        extra = {} if org is None else {"HTTP_X_ORGANIZATION": org}
        return self.client.post(URL, {field: upload}, format="multipart", **extra)

    def upload_sample(self, org="northwind"):
        return self.upload(SAMPLE.read_bytes(), org=org)

    def outcomes(self, org_slug="northwind"):
        txns = BankTransaction.objects.filter(
            organization__slug=org_slug
        ).prefetch_related("matches__line_item")
        return {
            t.bank_reference: (
                t.status,
                sorted(m.line_item.reference for m in t.matches.all()),
            )
            for t in txns
        }


class RejectionTests(ImportAPITestCase):
    def test_missing_column_rejects_file_and_saves_nothing(self):
        body = "date,description,amount,type\n2026-04-03,WIRE,100.00,credit\n"
        response = self.upload(body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("reference", response.json()["detail"])
        self.assertEqual(BankTransaction.objects.count(), 0)
        self.assertEqual(TransactionMatch.objects.count(), 0)

    def test_missing_file_field_is_400(self):
        self.assertEqual(self.upload(HEADER, field="not_file").status_code, 400)

    def test_blank_file_is_400(self):
        self.assertEqual(self.upload("  \n").status_code, 400)

    def test_missing_org_header_is_400_and_saves_nothing(self):
        response = self.upload(csv_rows("2026-04-03,A,10.00,credit,R1"), org=None)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_unknown_org_is_404(self):
        response = self.upload(csv_rows("2026-04-03,A,10.00,credit,R1"), org="nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(BankTransaction.objects.count(), 0)


class SampleFileTests(ImportAPITestCase):
    EXPECTED_NORTHWIND = {
        "FT20260403A": ("suggested", ["CC-1002"]),
        "ACH20260404B": ("suggested", ["CC-1001"]),
        "FEE20260405": ("ignored", []),
        "FT20260407C": ("suggested", ["CC-1003"]),
        "FT20260408E": ("suggested", ["CC-1004"]),
        "FT20260409D": ("needs_review", []),
        "FT20260411G": ("needs_review", ["CC-1005", "CC-1006"]),
        "ACH20260414H": ("suggested", ["CC-1001"]),
        "FT20260415J": ("needs_review", []),
        "FT20260416K": ("needs_review", []),
        "FT20260320N": ("needs_review", []),
    }

    def test_northwind_summary(self):
        response = self.upload_sample()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["imported"], 11)
        self.assertEqual(data["duplicates"], 1)
        self.assertEqual(
            data["duplicate_rows"], [{"line": 11, "bank_reference": "FT20260403A"}]
        )
        self.assertEqual([e["line"] for e in data["errors"]], [13, 14])
        self.assertIn("calendar date", data["errors"][0]["message"])
        self.assertIn("Credit amount must be positive", data["errors"][1]["message"])
        self.assertEqual(
            data["by_status"], {"suggested": 5, "needs_review": 5, "ignored": 1}
        )
        self.assertEqual(BankTransaction.objects.count(), 11)

    def test_northwind_outcomes(self):
        self.upload_sample()
        self.assertEqual(self.outcomes(), self.EXPECTED_NORTHWIND)

    def test_reasons_amounts_trimming_and_no_funding(self):
        self.upload_sample()

        for txn in BankTransaction.objects.prefetch_related("matches"):
            if txn.status == "needs_review":
                self.assertTrue(txn.reason, txn.bank_reference)
            if txn.status == "suggested":
                self.assertEqual(txn.reason, "")
            for match in txn.matches.all():
                self.assertTrue(match.reason)
                self.assertEqual(match.status, "proposed")

        paid = BankTransaction.objects.get(bank_reference="FT20260403A")
        self.assertIsInstance(paid.amount, Decimal)
        self.assertEqual(paid.amount, Decimal("25000.00"))

        meridian = BankTransaction.objects.get(bank_reference="FT20260408E")
        self.assertEqual(meridian.description, "wire incoming meridian partners")

        partial = TransactionMatch.objects.get(transaction__bank_reference="ACH20260414H")
        self.assertIn("partial payment 7,500.00 of remaining 27,500.00", partial.reason)

        overpaid = BankTransaction.objects.get(bank_reference="FT20260415J")
        self.assertIn("overpayment", overpaid.reason)

        # Importing only suggests; funding happens on approval.
        self.assertFalse(LineItem.objects.exclude(amount_funded=0).exists())
        self.assertEqual(set(LineItem.objects.values_list("status", flat=True)), {"open"})

    def test_bluebird_gets_its_own_suggestion(self):
        data = self.upload_sample(org="bluebird").json()
        self.assertEqual(data["imported"], 11)
        self.assertEqual(
            data["by_status"], {"suggested": 1, "needs_review": 9, "ignored": 1}
        )
        self.assertEqual(
            self.outcomes("bluebird")["FT20260416K"], ("suggested", ["CC-2001"])
        )
        self.assertFalse(
            TransactionMatch.objects.exclude(
                line_item__organization__slug="bluebird"
            ).exists()
        )

    def test_same_references_are_allowed_in_different_orgs(self):
        self.upload_sample("northwind")
        data = self.upload_sample("bluebird").json()
        self.assertEqual(data["imported"], 11)
        self.assertEqual(data["duplicates"], 1)  # only the repeat inside the file
        self.assertEqual(BankTransaction.objects.count(), 22)

    def test_reupload_is_all_duplicates(self):
        self.upload_sample()
        data = self.upload_sample().json()
        self.assertEqual(data["imported"], 0)
        self.assertEqual(data["duplicates"], 12)
        self.assertEqual(len(data["errors"]), 2)
        self.assertEqual(
            data["by_status"], {"suggested": 0, "needs_review": 0, "ignored": 0}
        )
        self.assertEqual(BankTransaction.objects.count(), 11)


class RuleTests(ImportAPITestCase):
    def test_duplicate_inside_one_file_first_wins(self):
        body = csv_rows(
            "2026-04-03,FIRST,10.00,credit,DUP1",
            "2026-04-04,SECOND,20.00,credit,DUP1",
        )
        data = self.upload(body).json()
        self.assertEqual((data["imported"], data["duplicates"]), (1, 1))
        self.assertEqual(
            data["duplicate_rows"], [{"line": 3, "bank_reference": "DUP1"}]
        )
        self.assertEqual(BankTransaction.objects.get().description, "FIRST")

    def test_invalid_row_does_not_claim_its_reference(self):
        body = csv_rows(
            "2026-04-03,BAD AMOUNT,oops,credit,REF1",
            "2026-04-04,GOOD,10.00,credit,REF1",
        )
        data = self.upload(body).json()
        self.assertEqual((data["imported"], data["duplicates"]), (1, 0))
        self.assertEqual([e["line"] for e in data["errors"]], [2])

    def test_bank_references_are_compared_exactly(self):
        body = csv_rows(
            "2026-04-03,A,10.00,credit,ft1",
            "2026-04-03,B,10.00,credit,FT1",
        )
        data = self.upload(body).json()
        self.assertEqual((data["imported"], data["duplicates"]), (2, 0))

    def test_debit_is_ignored_and_never_matched(self):
        body = csv_rows(
            "2026-04-05,PAYMENT TO RAHUL SHAH TRUST CC-1002,-25000.00,debit,DEB1"
        )
        data = self.upload(body).json()
        self.assertEqual(data["by_status"]["ignored"], 1)
        txn = BankTransaction.objects.get()
        self.assertEqual((txn.status, txn.type), ("ignored", "debit"))
        self.assertTrue(txn.reason)
        self.assertFalse(txn.matches.exists())

    def test_large_file_uses_a_fixed_number_of_queries(self):
        rows = [
            f"2026-04-09,UNKNOWN WIRE {i},{10 + i}.00,credit,BULK{i}"
            for i in range(3000)
        ]
        with CaptureQueriesContext(connection) as queries:
            data = self.upload(csv_rows(*rows)).json()
        self.assertEqual(data["imported"], 3000)
        self.assertEqual(BankTransaction.objects.count(), 3000)
        self.assertLess(len(queries), 25)