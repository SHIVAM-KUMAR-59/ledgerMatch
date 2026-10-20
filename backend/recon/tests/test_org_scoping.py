from io import StringIO

from django.core.management import call_command
from rest_framework.test import APITestCase

URL = "/api/line-items/"


class OrgScopingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())

    def get(self, org=None):
        extra = {} if org is None else {"HTTP_X_ORGANIZATION": org}
        return self.client.get(URL, **extra)

    def test_missing_header_is_400(self):
        self.assertEqual(self.get().status_code, 400)

    def test_blank_header_is_400(self):
        self.assertEqual(self.get("   ").status_code, 400)

    def test_unknown_org_is_404(self):
        self.assertEqual(self.get("nope").status_code, 404)

    def test_slug_is_case_sensitive(self):
        self.assertEqual(self.get("Northwind").status_code, 404)

    def test_northwind_sees_only_its_items(self):
        data = self.get("northwind").json()
        self.assertEqual(
            [i["reference"] for i in data],
            ["CC-1001", "CC-1002", "CC-1003", "CC-1004", "CC-1005", "CC-1006"],
        )

    def test_bluebird_sees_only_its_item(self):
        data = self.get("bluebird").json()
        self.assertEqual([i["reference"] for i in data], ["CC-2001"])

    def test_money_is_serialized_as_decimal_strings(self):
        first = self.get("northwind").json()[0]
        self.assertEqual(first["amount_called"], "27500.00")
        self.assertEqual(first["remaining"], "27500.00")