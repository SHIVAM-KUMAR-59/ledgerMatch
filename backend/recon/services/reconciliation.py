"""Approve, reject and ignore.

This is the only code that changes funding or a transaction's status after
import. Every operation runs in one database transaction and takes row locks
in a fixed order, BankTransaction first and LineItem second, so concurrent
requests queue up instead of deadlocking or double-funding.

The module knows nothing about HTTP: it raises ReconciliationNotFound and
ReconciliationConflict, and the API layer maps those to 404 and 409.
"""

from django.db import transaction

from recon.models import BankTransaction, LineItem, TransactionMatch

TXN = BankTransaction.Status
MATCH = TransactionMatch.Status
LINE = LineItem.Status

# Only these transactions can still be approved, rejected or ignored.
ACTIONABLE = (TXN.SUGGESTED, TXN.NEEDS_REVIEW)

ALL_REJECTED_REASON = "All suggested matches were rejected."
IGNORED_REASON = "Ignored by the user."


class ReconciliationNotFound(Exception):
    pass


class ReconciliationConflict(Exception):
    pass


def approve_match(organization, match_id):
    with transaction.atomic():
        txn, match = _lock_transaction_and_match(organization, match_id)
        _require_actionable(txn, match)

        # Second lock: the line item. Its remaining amount is re-read here,
        # under the lock, so a payment approved a moment ago is already counted.
        item = LineItem.objects.select_for_update().get(
            pk=match.line_item_id, organization=organization
        )
        if txn.amount > item.remaining:
            raise ReconciliationConflict(
                f"Amount {txn.amount:,.2f} exceeds remaining "
                f"{item.remaining:,.2f} on {item.reference}."
            )

        item.amount_funded += txn.amount
        _refresh_line_status(item)
        item.save(update_fields=["amount_funded", "status"])

        match.status = MATCH.APPROVED
        match.save(update_fields=["status"])
        txn.matches.exclude(pk=match.pk).update(status=MATCH.REJECTED)

        txn.status = TXN.RECONCILED
        txn.reason = ""  # the approved match keeps its own reason
        txn.save(update_fields=["status", "reason"])
    return txn


def reject_match(organization, match_id):
    with transaction.atomic():
        txn, match = _lock_transaction_and_match(organization, match_id)
        _require_actionable(txn, match)

        match.status = MATCH.REJECTED
        match.save(update_fields=["status"])

        if not txn.matches.filter(status=MATCH.PROPOSED).exists():
            txn.status = TXN.NEEDS_REVIEW
            txn.reason = ALL_REJECTED_REASON
            txn.save(update_fields=["status", "reason"])
    return txn


def ignore_transaction(organization, transaction_id):
    with transaction.atomic():
        txn = (
            BankTransaction.objects.select_for_update()
            .filter(pk=transaction_id, organization=organization)
            .first()
        )
        if txn is None:
            raise ReconciliationNotFound("Transaction not found.")
        if txn.status not in ACTIONABLE:
            raise ReconciliationConflict(
                f"Transaction is {txn.status} and cannot be ignored."
            )

        txn.matches.filter(status=MATCH.PROPOSED).update(status=MATCH.REJECTED)
        txn.status = TXN.IGNORED
        txn.reason = IGNORED_REASON
        txn.save(update_fields=["status", "reason"])
    return txn


def _lock_transaction_and_match(organization, match_id):
    """Find the match (scoped to the org), lock its transaction, then re-read
    the match under that lock so the checks see the latest committed state."""
    txn_id = (
        TransactionMatch.objects.filter(
            pk=match_id, transaction__organization=organization
        )
        .values_list("transaction_id", flat=True)
        .first()
    )
    if txn_id is None:
        raise ReconciliationNotFound("Match not found.")
    txn = BankTransaction.objects.select_for_update().get(pk=txn_id)
    match = TransactionMatch.objects.get(pk=match_id)
    return txn, match


def _require_actionable(txn, match):
    if txn.status not in ACTIONABLE:
        raise ReconciliationConflict(f"Transaction is already {txn.status}.")
    if match.status != MATCH.PROPOSED:
        raise ReconciliationConflict(f"Match is already {match.status}.")


def _refresh_line_status(item):
    """The one place a line item's status is derived from its funded amount."""
    if item.remaining == 0:
        item.status = LINE.FUNDED
    elif item.amount_funded > 0:
        item.status = LINE.PARTIALLY_FUNDED
    else:
        item.status = LINE.OPEN