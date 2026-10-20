from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from recon.models import Organization
from recon.services.csv_import import import_statement

URL = "/api/transactions/"
SAMPLE = Path(__file__).parent / "data" / "sample_bank_statement.csv"


class TransactionListTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        for slug in ("northwind", "bluebird"):
            import_statement(Organization.objects.get(slug=slug), SAMPLE.read_bytes())

    def get(self, org="northwind"):
        extra = {} if org is None else {"HTTP_X_ORGANIZATION": org}
        return self.client.get(URL, **extra)

    def test_missing_header_is_400_and_unknown_org_is_404(self):
        self.assertEqual(self.get(None).status_code, 400)
        self.assertEqual(self.get("nope").status_code, 404)

    def test_newest_first(self):
        data = self.get().json()
        self.assertEqual(
            [t["bank_reference"] for t in data],
            [
                "FT20260416K", "FT20260415J", "ACH20260414H", "FT20260411G",
                "FT20260409D", "FT20260408E", "FT20260407C", "FEE20260405",
                "ACH20260404B", "FT20260403A", "FT20260320N",
            ],
        )

    def test_matches_and_reasons_are_included(self):
        by_ref = {t["bank_reference"]: t for t in self.get().json()}

        paid = by_ref["FT20260403A"]
        self.assertEqual(paid["status"], "suggested")
        self.assertEqual(paid["amount"], "25000.00")
        self.assertEqual(len(paid["matches"]), 1)
        match = paid["matches"][0]
        self.assertEqual(match["line_item"]["reference"], "CC-1002")
        self.assertEqual(match["line_item"]["investor_name"], "Rahul Shah Trust")
        self.assertEqual(match["status"], "proposed")
        self.assertIn("Reference CC-1002 found", match["reason"])

        oakridge = by_ref["FT20260411G"]
        self.assertEqual(oakridge["status"], "needs_review")
        self.assertEqual(
            [m["line_item"]["reference"] for m in oakridge["matches"]],
            ["CC-1005", "CC-1006"],
        )
        self.assertTrue(oakridge["reason"])

        self.assertEqual(by_ref["FT20260409D"]["matches"], [])
        self.assertEqual(by_ref["FEE20260405"]["status"], "ignored")

    def test_each_org_sees_only_its_own_transactions(self):
        northwind = self.get("northwind").json()
        bluebird = self.get("bluebird").json()
        self.assertEqual((len(northwind), len(bluebird)), (11, 11))
        self.assertFalse(
            {t["id"] for t in northwind} & {t["id"] for t in bluebird}
        )
        cedar_nw = next(t for t in northwind if t["bank_reference"] == "FT20260416K")
        cedar_bb = next(t for t in bluebird if t["bank_reference"] == "FT20260416K")
        self.assertEqual(cedar_nw["matches"], [])
        self.assertEqual(cedar_bb["matches"][0]["line_item"]["reference"], "CC-2001")

    def test_query_count_does_not_grow_with_rows(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.get().status_code, 200)
        # org lookup + transactions + matches + line items
        self.assertLessEqual(len(queries), 5)