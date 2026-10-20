import datetime as dt
import io
import json
import urllib.error
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from recon.integrations.airtable import (
    AirtableClient,
    AirtableError,
    build_airtable_client,
)
from recon.models import BankTransaction, Organization
from recon.services.airtable_sync import sync_needs_review

URLOPEN = "recon.integrations.airtable.urllib.request.urlopen"


class FakeAirtableClient:
    """Records every create call. Fails on call number `fail_on_call`, if given."""

    def __init__(self, fail_on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def create_records(self, records):
        self.calls.append(records)
        if self.fail_on_call == len(self.calls):
            raise AirtableError("boom")
        offset = sum(len(call) for call in self.calls[:-1])
        return [f"rec{offset + i:04d}" for i in range(len(records))]


def make_txn(org, n, status="needs_review", sync_status="not_synced"):
    return BankTransaction.objects.create(
        organization=org,
        date=dt.date(2026, 4, 9),
        description=f"UNKNOWN WIRE {n}",
        amount=Decimal("9999.99"),
        type="credit",
        bank_reference=f"REF{n:04d}",
        status=status,
        reason="No reference found and no line item matches both investor name and amount.",
        sync_status=sync_status,
    )


def statuses(org):
    return list(
        BankTransaction.objects.filter(organization=org)
        .order_by("id")
        .values_list("sync_status", flat=True)
    )


def sync(client, org, **kwargs):
    return sync_needs_review(client, org, pause=0, **kwargs)


class SyncServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        cls.northwind = Organization.objects.get(slug="northwind")
        cls.bluebird = Organization.objects.get(slug="bluebird")

    def test_syncs_needs_review_rows_and_stores_record_ids(self):
        for n in range(3):
            make_txn(self.northwind, n)
        client = FakeAirtableClient()

        result = sync(client, self.northwind)

        self.assertEqual(
            result,
            {"enabled": True, "synced": 3, "failed": 0, "remaining": 0, "error": None},
        )
        self.assertEqual(len(client.calls), 1)
        for txn in BankTransaction.objects.all():
            self.assertEqual(txn.sync_status, "synced")
            self.assertTrue(txn.airtable_record_id.startswith("rec"))
            self.assertEqual(txn.sync_error, "")

    def test_payload_fields_are_complete_and_all_strings(self):
        make_txn(self.northwind, 1)
        client = FakeAirtableClient()
        sync(client, self.northwind)

        self.assertEqual(
            client.calls[0][0],
            {
                "Bank Reference": "REF0001",
                "Organization": "northwind",
                "Date": "2026-04-09",
                "Description": "UNKNOWN WIRE 1",
                "Amount": 9999.99,
                "Reason": "No reference found and no line item matches both investor name and amount.",
            },
        )

    def test_only_needs_review_rows_are_sent(self):
        make_txn(self.northwind, 1, status="suggested")
        make_txn(self.northwind, 2, status="reconciled")
        make_txn(self.northwind, 3, status="ignored")
        make_txn(self.northwind, 4, status="needs_review")
        client = FakeAirtableClient()

        result = sync(client, self.northwind)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(
            [r["Bank Reference"] for r in client.calls[0]], ["REF0004"]
        )

    def test_rows_are_sent_in_batches_of_ten(self):
        for n in range(25):
            make_txn(self.northwind, n)
        client = FakeAirtableClient()

        sync(client, self.northwind)

        self.assertEqual([len(call) for call in client.calls], [10, 10, 5])
        self.assertEqual(set(statuses(self.northwind)), {"synced"})

    def test_limit_caps_the_run_and_reports_what_is_left(self):
        for n in range(25):
            make_txn(self.northwind, n)
        client = FakeAirtableClient()

        result = sync(client, self.northwind, limit=12)

        self.assertEqual([len(call) for call in client.calls], [10, 2])
        self.assertEqual((result["synced"], result["remaining"]), (12, 13))
        self.assertEqual(statuses(self.northwind), ["synced"] * 12 + ["not_synced"] * 13)

    def test_failure_marks_the_batch_failed_stops_and_does_not_raise(self):
        for n in range(25):
            make_txn(self.northwind, n)
        client = FakeAirtableClient(fail_on_call=2)

        result = sync(client, self.northwind)

        self.assertEqual(len(client.calls), 2)  # stopped after the failure
        self.assertEqual(
            (result["synced"], result["failed"], result["remaining"]), (10, 10, 5)
        )
        self.assertEqual(result["error"], "boom")
        self.assertEqual(
            statuses(self.northwind),
            ["synced"] * 10 + ["sync_failed"] * 10 + ["not_synced"] * 5,
        )
        failed = BankTransaction.objects.filter(sync_status="sync_failed").first()
        self.assertEqual(failed.sync_error, "boom")
        self.assertEqual(failed.airtable_record_id, "")

    def test_synced_and_failed_rows_are_never_resent(self):
        for n in range(25):
            make_txn(self.northwind, n)
        sync(FakeAirtableClient(fail_on_call=2), self.northwind)

        second = FakeAirtableClient()
        result = sync(second, self.northwind)

        self.assertEqual([len(call) for call in second.calls], [5])
        self.assertEqual((result["synced"], result["remaining"]), (5, 0))
        self.assertEqual(
            statuses(self.northwind),
            ["synced"] * 10 + ["sync_failed"] * 10 + ["synced"] * 5,
        )

    def test_other_organizations_rows_are_untouched(self):
        make_txn(self.northwind, 1)
        other = make_txn(self.bluebird, 2)
        client = FakeAirtableClient()

        sync(client, self.northwind)

        other.refresh_from_db()
        self.assertEqual(other.sync_status, "not_synced")
        self.assertEqual(len(client.calls[0]), 1)

    def test_wrong_number_of_ids_counts_as_a_failure(self):
        class ShortClient:
            def create_records(self, records):
                return ["rec1"]

        for n in range(3):
            make_txn(self.northwind, n)

        result = sync(ShortClient(), self.northwind)

        self.assertEqual((result["synced"], result["failed"]), (0, 3))
        self.assertEqual(set(statuses(self.northwind)), {"sync_failed"})

    def test_nothing_pending_makes_no_calls(self):
        client = FakeAirtableClient()
        result = sync(client, self.northwind)
        self.assertEqual(client.calls, [])
        self.assertEqual((result["synced"], result["failed"], result["remaining"]), (0, 0, 0))

    def test_sync_does_not_overwrite_a_status_changed_in_the_meantime(self):
        class ApprovingClient(FakeAirtableClient):
            def create_records(self, records):
                # Simulates a GP reconciling the row while the request is in flight.
                BankTransaction.objects.filter(bank_reference="REF0001").update(
                    status="reconciled"
                )
                return super().create_records(records)

        make_txn(self.northwind, 1)
        sync(ApprovingClient(), self.northwind)

        txn = BankTransaction.objects.get(bank_reference="REF0001")
        self.assertEqual(txn.status, "reconciled")
        self.assertEqual(txn.sync_status, "synced")


class AirtableClientTests(SimpleTestCase):
    def make_client(self):
        return AirtableClient("pat_secret", "appBASE", "Needs Review")

    def response(self, body):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        return response

    def test_request_shape_and_returned_ids(self):
        with patch(URLOPEN) as urlopen:
            urlopen.return_value = self.response(
                {"records": [{"id": "rec1"}, {"id": "rec2"}]}
            )
            ids = self.make_client().create_records([{"A": "1"}, {"A": "2"}])

        self.assertEqual(ids, ["rec1", "rec2"])
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://api.airtable.com/v0/appBASE/Needs%20Review"
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer pat_secret")
        self.assertEqual(
            json.loads(request.data),
            {"records": [{"fields": {"A": "1"}}, {"fields": {"A": "2"}}]},
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)

    def test_empty_list_makes_no_request(self):
        with patch(URLOPEN) as urlopen:
            self.assertEqual(self.make_client().create_records([]), [])
        urlopen.assert_not_called()

    def test_http_error_becomes_airtable_error_without_the_token(self):
        error = urllib.error.HTTPError(
            "https://api.airtable.com",
            422,
            "Unprocessable",
            {},
            io.BytesIO(b'{"error": "UNKNOWN_FIELD_NAME"}'),
        )
        with patch(URLOPEN, side_effect=error):
            with self.assertRaises(AirtableError) as ctx:
                self.make_client().create_records([{"A": "1"}])

        message = str(ctx.exception)
        self.assertIn("422", message)
        self.assertIn("UNKNOWN_FIELD_NAME", message)
        self.assertNotIn("pat_secret", message)

    def test_network_error_becomes_airtable_error(self):
        with patch(URLOPEN, side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(AirtableError):
                self.make_client().create_records([{"A": "1"}])

    def test_timeout_becomes_airtable_error(self):
        with patch(URLOPEN, side_effect=TimeoutError("timed out")):
            with self.assertRaises(AirtableError):
                self.make_client().create_records([{"A": "1"}])

    def test_non_json_response_becomes_airtable_error(self):
        with patch(URLOPEN) as urlopen:
            urlopen.return_value = self.response(b"<html>gateway error</html>")
            with self.assertRaises(AirtableError):
                self.make_client().create_records([{"A": "1"}])

    def test_unexpected_payload_becomes_airtable_error(self):
        with patch(URLOPEN) as urlopen:
            urlopen.return_value = self.response({"nothing": 1})
            with self.assertRaises(AirtableError):
                self.make_client().create_records([{"A": "1"}])

    def test_more_than_ten_records_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.make_client().create_records([{"A": str(i)} for i in range(11)])


class BuildClientTests(SimpleTestCase):
    @override_settings(AIRTABLE_TOKEN="", AIRTABLE_BASE_ID="", AIRTABLE_TABLE="")
    def test_none_when_not_configured(self):
        self.assertIsNone(build_airtable_client())

    @override_settings(AIRTABLE_TOKEN="t", AIRTABLE_BASE_ID="", AIRTABLE_TABLE="")
    def test_none_when_only_partly_configured(self):
        self.assertIsNone(build_airtable_client())

    @override_settings(AIRTABLE_TOKEN="t", AIRTABLE_BASE_ID="app1", AIRTABLE_TABLE="Tbl")
    def test_client_when_fully_configured(self):
        self.assertIsInstance(build_airtable_client(), AirtableClient)