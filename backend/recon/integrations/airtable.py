"""Minimal Airtable client: create records only.

Uses the standard library, so there is no extra dependency. Everything that can
go wrong raises AirtableError, and its message never contains the token.
"""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

API_ROOT = "https://api.airtable.com/v0"
MAX_RECORDS_PER_REQUEST = 10  # Airtable's limit for one create call


class AirtableError(Exception):
    pass


class AirtableClient:
    def __init__(self, token, base_id, table, timeout=10):
        self._token = token
        self._timeout = timeout
        self._url = "/".join(
            [
                API_ROOT,
                urllib.parse.quote(base_id, safe=""),
                urllib.parse.quote(table, safe=""),
            ]
        )

    def create_records(self, records):
        """Create one record per dict in `records` (field name -> value).

        Returns the new record ids in the same order. At most 10 records.
        """
        if len(records) > MAX_RECORDS_PER_REQUEST:
            raise ValueError(
                f"Airtable accepts at most {MAX_RECORDS_PER_REQUEST} records per request."
            )
        if not records:
            return []

        body = json.dumps(
            {"records": [{"fields": fields} for fields in records]}
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise AirtableError(
                f"Airtable returned HTTP {exc.code}: {_error_detail(exc)}"
            ) from None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise AirtableError(f"Could not use Airtable: {exc}") from None

        try:
            return [record["id"] for record in payload["records"]]
        except (KeyError, TypeError):
            raise AirtableError("Unexpected response from Airtable.") from None


def _error_detail(exc):
    try:
        text = exc.read().decode("utf-8", "replace").strip()
    except Exception:  # reading the error body must never hide the error itself
        text = ""
    return (text or str(exc.reason))[:300]


def build_airtable_client():
    """The configured client, or None when Airtable is not (fully) configured."""
    if not (
        settings.AIRTABLE_TOKEN and settings.AIRTABLE_BASE_ID and settings.AIRTABLE_TABLE
    ):
        return None
    return AirtableClient(
        settings.AIRTABLE_TOKEN, settings.AIRTABLE_BASE_ID, settings.AIRTABLE_TABLE
    )