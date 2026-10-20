import logging
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from recon.models import BankTransaction, Organization
from recon.tests.test_airtable import FakeAirtableClient, make_txn

SAMPLE = Path(__file__).parent / "data" / "sample_bank_statement.csv"
# The needs_review rows the sample file produces for northwind.
NEEDS_REVIEW_REFS = {
    "FT20260409D", "FT20260411G", "FT20260415J", "FT20260416K", "FT20260320N",
}
CLIENT = "recon.services.airtable_sync.build_airtable_client"
COMMAND_CLIENT = "recon.management.commands.sync_airtable.build_airtable_client"


class AirtableImportTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())

    def setUp(self):
        logging.disable(logging.CRITICAL)  # keep expected failure logs out of the output
        self.addCleanup(logging.disable, logging.NOTSET)

    def upload(self, org="northwind"):
        upload = SimpleUploadedFile(
            "statement.csv", SAMPLE.read_bytes(), content_type="text/csv"
        )
        return self.client.post(
            "/api/transactions/import/",
            {"file": upload},
            format="multipart",
            HTTP_X_ORGANIZATION=org,
        )

    def test_import_syncs_needs_review_rows(self):
        fake = FakeAirtableClient()
        with patch(CLIENT, return_value=fake):
            data = self.upload().json()

        self.assertEqual(data["imported"], 11)
        self.assertEqual(
            data["airtable"],
            {"enabled": True, "synced": 5, "failed": 0, "remaining": 0, "error": None},
        )
        sent = {r["Bank Reference"] for call in fake.calls for r in call}
        self.assertEqual(sent, NEEDS_REVIEW_REFS)

        synced = BankTransaction.objects.filter(sync_status="synced")
        self.assertEqual({t.bank_reference for t in synced}, NEEDS_REVIEW_REFS)
        self.assertTrue(all(t.airtable_record_id.startswith("rec") for t in synced))
        # Suggested and ignored rows are never sent.
        self.assertEqual(
            BankTransaction.objects.filter(sync_status="not_synced").count(), 6
        )

    def test_airtable_failure_never_breaks_the_import(self):
        with patch(CLIENT, return_value=FakeAirtableClient(fail_on_call=1)):
            response = self.upload()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["imported"], data["duplicates"]), (11, 1))
        self.assertEqual(data["airtable"]["synced"], 0)
        self.assertEqual(data["airtable"]["failed"], 5)
        self.assertEqual(data["airtable"]["error"], "boom")
        self.assertEqual(BankTransaction.objects.count(), 11)
        failed = BankTransaction.objects.filter(sync_status="sync_failed")
        self.assertEqual(failed.count(), 5)
        self.assertEqual(set(failed.values_list("sync_error", flat=True)), {"boom"})

    def test_unexpected_sync_crash_never_breaks_the_import(self):
        with patch(CLIENT, return_value=FakeAirtableClient()), patch(
            "recon.services.airtable_sync.sync_needs_review",
            side_effect=RuntimeError("db down"),
        ):
            response = self.upload()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["imported"], 11)
        self.assertTrue(data["airtable"]["enabled"])
        self.assertTrue(data["airtable"]["error"])
        self.assertEqual(BankTransaction.objects.count(), 11)

    @override_settings(AIRTABLE_TOKEN="", AIRTABLE_BASE_ID="", AIRTABLE_TABLE="")
    def test_disabled_when_not_configured(self):
        data = self.upload().json()

        self.assertEqual(data["airtable"], {"enabled": False})
        self.assertEqual(data["imported"], 11)
        self.assertFalse(
            BankTransaction.objects.exclude(sync_status="not_synced").exists()
        )

    @override_settings(AIRTABLE_MAX_PER_IMPORT=2)
    def test_cap_per_import_is_reported_as_remaining(self):
        fake = FakeAirtableClient()
        with patch(CLIENT, return_value=fake):
            data = self.upload().json()

        self.assertEqual(
            (data["airtable"]["synced"], data["airtable"]["remaining"]), (2, 3)
        )
        self.assertEqual([len(call) for call in fake.calls], [2])

    def test_reupload_sends_nothing_new(self):
        fake = FakeAirtableClient()
        with patch(CLIENT, return_value=fake):
            self.upload()
            calls_after_first = len(fake.calls)
            data = self.upload().json()

        self.assertEqual(calls_after_first, 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(data["imported"], 0)
        self.assertEqual(data["airtable"]["synced"], 0)

    def test_transaction_list_exposes_sync_fields(self):
        with patch(CLIENT, return_value=FakeAirtableClient()):
            self.upload()

        rows = self.client.get(
            "/api/transactions/", HTTP_X_ORGANIZATION="northwind"
        ).json()
        by_ref = {row["bank_reference"]: row for row in rows}
        self.assertEqual(by_ref["FT20260409D"]["sync_status"], "synced")
        self.assertTrue(by_ref["FT20260409D"]["airtable_record_id"].startswith("rec"))
        self.assertEqual(by_ref["FT20260403A"]["sync_status"], "not_synced")


class SyncAirtableCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        cls.northwind = Organization.objects.get(slug="northwind")
        cls.bluebird = Organization.objects.get(slug="bluebird")

    def run_command(self, *args, client):
        out = StringIO()
        with patch(COMMAND_CLIENT, return_value=client):
            call_command("sync_airtable", *args, stdout=out)
        return out.getvalue()

    def test_fails_clearly_when_airtable_is_not_configured(self):
        with self.assertRaises(CommandError):
            self.run_command(client=None)

    def test_syncs_pending_rows_for_every_organization(self):
        make_txn(self.northwind, 1)
        make_txn(self.bluebird, 2)

        output = self.run_command(client=FakeAirtableClient())

        self.assertEqual(
            set(BankTransaction.objects.values_list("sync_status", flat=True)),
            {"synced"},
        )
        self.assertIn("northwind: 1 synced", output)
        self.assertIn("bluebird: 1 synced", output)

    def test_org_option_limits_the_run(self):
        make_txn(self.northwind, 1)
        other = make_txn(self.bluebird, 2)

        self.run_command("--org", "northwind", client=FakeAirtableClient())

        other.refresh_from_db()
        self.assertEqual(other.sync_status, "not_synced")

    def test_unknown_org_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_command("--org", "nope", client=FakeAirtableClient())

    def test_failed_rows_are_only_resent_with_retry_failed(self):
        txn = make_txn(self.northwind, 1, sync_status="sync_failed")
        txn.sync_error = "old error"
        txn.save()

        fake = FakeAirtableClient()
        self.run_command(client=fake)
        txn.refresh_from_db()
        self.assertEqual(fake.calls, [])
        self.assertEqual(txn.sync_status, "sync_failed")

        self.run_command("--retry-failed", client=fake)
        txn.refresh_from_db()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(txn.sync_status, "synced")
        self.assertEqual(txn.sync_error, "")