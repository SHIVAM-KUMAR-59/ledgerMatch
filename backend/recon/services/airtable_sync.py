"""Push needs_review transactions to Airtable (optional bonus).

Best effort and create-only. This runs after an import has committed, so a
failure here can never undo or break the import. The client is built in one
place (build_airtable_client) and passed in, so tests hand it a fake and never
touch the network.
"""

import logging
import time

from django.conf import settings

from recon.integrations.airtable import AirtableError, build_airtable_client
from recon.models import BankTransaction

logger = logging.getLogger(__name__)

BATCH_SIZE = 10  # Airtable's limit for one create request
PAUSE_SECONDS = 0.25  # stay under Airtable's 5 requests per second per base

TXN = BankTransaction.Status
SYNC = BankTransaction.SyncStatus
SYNC_FIELDS = ["airtable_record_id", "sync_status", "sync_error"]


def sync_after_import(organization):
    """Entry point for the import view. Always returns a summary dict and never
    raises, whatever goes wrong: the import has already been saved."""
    try:
        client = build_airtable_client()
        if client is None:
            return {"enabled": False}
        return sync_needs_review(
            client, organization, limit=settings.AIRTABLE_MAX_PER_IMPORT
        )
    except Exception:
        logger.exception("Airtable sync crashed after an import")
        return {
            "enabled": True,
            "synced": 0,
            "failed": 0,
            "remaining": None,
            "error": "Sync could not run; the import itself succeeded.",
        }


def sync_needs_review(client, organization, limit=None, pause=PAUSE_SECONDS):
    """Send up to `limit` of the org's not-yet-synced needs_review rows.

    Never raises for an Airtable problem: the failing batch is marked
    sync_failed, the rest stay not_synced, and the result says what happened.
    """
    pending = list(_pending(organization).order_by("id")[:limit])

    synced = failed = 0
    error = None
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        if start:
            time.sleep(pause)
        try:
            ids = client.create_records([_fields(organization, t) for t in batch])
            if len(ids) != len(batch):
                raise AirtableError(
                    f"Airtable returned {len(ids)} ids for {len(batch)} records."
                )
        except Exception as exc:  # best effort: any failure is recorded, never raised
            logger.warning("Airtable sync failed: %s", exc, exc_info=True)
            error = str(exc)
            _mark_failed(batch, error)
            failed += len(batch)
            break  # leave the remaining rows not_synced for a later run
        _mark_synced(batch, ids)
        synced += len(batch)

    return {
        "enabled": True,
        "synced": synced,
        "failed": failed,
        "remaining": _pending(organization).count(),
        "error": error,
    }


def _pending(organization):
    return BankTransaction.objects.filter(
        organization=organization,
        status=TXN.NEEDS_REVIEW,
        sync_status=SYNC.NOT_SYNCED,
    )


def _fields(organization, txn):
    # Every value is a string, so no money ever passes through a float.
    return {
        "Bank Reference": txn.bank_reference,
        "Organization": organization.slug,
        "Date": txn.date.isoformat(),
        "Description": txn.description,
        "Amount": float(txn.amount),
        "Reason": txn.reason,
    }


# bulk_update writes only the sync columns. save() would write every column
# back, and could overwrite a status an approval changed while the request to
# Airtable was in flight.
def _mark_synced(batch, ids):
    for txn, record_id in zip(batch, ids):
        txn.airtable_record_id = record_id
        txn.sync_status = SYNC.SYNCED
        txn.sync_error = ""
    BankTransaction.objects.bulk_update(batch, SYNC_FIELDS)


def _mark_failed(batch, message):
    for txn in batch:
        txn.sync_status = SYNC.SYNC_FAILED
        txn.sync_error = message[:500]
    BankTransaction.objects.bulk_update(batch, SYNC_FIELDS)