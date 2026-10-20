"""Import a bank statement for one organization.

Parsing is pure and happens first (csv_parser): if the file is rejected,
nothing touches the database. Everything after that runs in one transaction
under a lock on the organization row, so two simultaneous uploads for the same
organization cannot both import the same bank reference.
"""

from collections import Counter

from django.db import transaction

from recon.models import BankTransaction, LineItem, Organization, TransactionMatch

from . import matching
from .csv_parser import parse_csv

BATCH_SIZE = 1000
DEBIT_REASON = "Debit; only credits can pay a capital call."

_SUGGESTED = BankTransaction.Status.SUGGESTED.value
_NEEDS_REVIEW = BankTransaction.Status.NEEDS_REVIEW.value
_IGNORED = BankTransaction.Status.IGNORED.value


def import_statement(organization, raw: bytes) -> dict:
    """Import `raw` CSV bytes for `organization` and return the summary.

    Raises csv_parser.FileRejected, before any database work, if the file as a
    whole is unusable.
    """
    parsed = parse_csv(raw)

    with transaction.atomic():
        # Serialise imports per organization so the duplicate check below
        # cannot race with another upload. The unique constraint on
        # (organization, bank_reference) remains as the backstop.
        Organization.objects.select_for_update().get(pk=organization.pk)

        seen = set(
            BankTransaction.objects.filter(organization=organization).values_list(
                "bank_reference", flat=True
            )
        )
        candidates = _load_candidates(organization)

        built = []  # (BankTransaction, MatchDecision or None)
        duplicates = []
        for row in parsed.rows:
            if row.reference in seen:
                duplicates.append({"line": row.line, "bank_reference": row.reference})
                continue
            seen.add(row.reference)
            built.append(_build_transaction(organization, row, candidates))

        # Two bulk inserts, however many rows. On PostgreSQL bulk_create sets
        # the primary keys, so the matches can point at their transactions.
        BankTransaction.objects.bulk_create(
            [txn for txn, _ in built], batch_size=BATCH_SIZE
        )
        TransactionMatch.objects.bulk_create(
            [
                TransactionMatch(
                    transaction=txn, line_item_id=m.line_item_id, reason=m.reason
                )
                for txn, decision in built
                if decision
                for m in decision.matches
            ],
            batch_size=BATCH_SIZE,
        )

    counts = Counter(txn.status for txn, _ in built)
    return {
        "imported": len(built),
        "duplicates": len(duplicates),
        "duplicate_rows": duplicates,
        "errors": [{"line": e.line, "message": e.message} for e in parsed.errors],
        "by_status": {
            status: counts.get(status, 0)
            for status in (_SUGGESTED, _NEEDS_REVIEW, _IGNORED)
        },
    }


def _load_candidates(organization):
    """Snapshot the org's line items once. 'Remaining' only changes on approval,
    never during an import, so every row can reuse this list."""
    items = LineItem.objects.filter(organization=organization).select_related(
        "capital_call"
    )
    return [
        matching.Candidate.build(
            id=item.id,
            reference=item.reference,
            investor_name=item.investor_name,
            remaining=item.remaining,
            call_date=item.capital_call.call_date,
        )
        for item in items
    ]


def _build_transaction(organization, row, candidates):
    txn = BankTransaction(
        organization=organization,
        date=row.date,
        description=row.description,
        amount=row.amount,
        type=row.type,
        bank_reference=row.reference,
    )
    if row.type == BankTransaction.Type.DEBIT:
        txn.status = _IGNORED
        txn.reason = DEBIT_REASON
        return txn, None

    decision = matching.decide(
        txn_date=row.date,
        amount=row.amount,
        description=row.description,
        bank_reference=row.reference,
        candidates=candidates,
    )
    txn.status = decision.status
    txn.reason = decision.reason
    return txn, decision