from decimal import Decimal

from django.db import models
from django.db.models import F, Q


class Organization(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.slug


class CapitalCall(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="capital_calls"
    )
    number = models.PositiveIntegerField()
    call_date = models.DateField()
    due_date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"], name="uniq_call_number_per_org"
            ),
        ]

    def __str__(self):
        return f"{self.organization} call #{self.number}"


class LineItem(models.Model):
    class Status(models.TextChoices):
        OPEN = "open"
        PARTIALLY_FUNDED = "partially_funded"
        FUNDED = "funded"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="line_items"
    )
    capital_call = models.ForeignKey(
        CapitalCall, on_delete=models.CASCADE, related_name="line_items"
    )
    investor_name = models.CharField(max_length=200)
    reference = models.CharField(max_length=50)
    amount_called = models.DecimalField(max_digits=14, decimal_places=2)
    amount_funded = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "reference"], name="uniq_line_ref_per_org"
            ),
            models.CheckConstraint(
                condition=Q(amount_funded__gte=0, amount_funded__lte=F("amount_called")),
                name="line_funded_within_called",
            ),
        ]

    @property
    def remaining(self):
        return self.amount_called - self.amount_funded

    def __str__(self):
        return f"{self.reference} ({self.investor_name})"


class BankTransaction(models.Model):
    class Status(models.TextChoices):
        SUGGESTED = "suggested"
        NEEDS_REVIEW = "needs_review"
        RECONCILED = "reconciled"
        IGNORED = "ignored"

    class Type(models.TextChoices):
        CREDIT = "credit"
        DEBIT = "debit"

    class SyncStatus(models.TextChoices):
        NOT_SYNCED = "not_synced"
        SYNCED = "synced"
        SYNC_FAILED = "sync_failed"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="transactions"
    )
    date = models.DateField()
    description = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    type = models.CharField(max_length=10, choices=Type.choices)
    bank_reference = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=Status.choices)
    reason = models.TextField(blank=True)
    sync_status = models.CharField(
        max_length=20, choices=SyncStatus.choices, default=SyncStatus.NOT_SYNCED
    )
    airtable_record_id = models.CharField(max_length=50, blank=True)
    sync_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["organization", "date"])]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "bank_reference"],
                name="uniq_bank_ref_per_org",
            ),
            models.CheckConstraint(
                condition=Q(type="credit", amount__gt=0)
                | Q(type="debit", amount__lt=0),
                name="txn_sign_matches_type",
            ),
        ]

    def __str__(self):
        return f"{self.bank_reference} {self.amount}"


class TransactionMatch(models.Model):
    class Status(models.TextChoices):
        PROPOSED = "proposed"
        APPROVED = "approved"
        REJECTED = "rejected"

    transaction = models.ForeignKey(
        BankTransaction, on_delete=models.CASCADE, related_name="matches"
    )
    line_item = models.ForeignKey(
        LineItem, on_delete=models.CASCADE, related_name="matches"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PROPOSED
    )
    reason = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transaction", "line_item"], name="uniq_match_per_pair"
            ),
            models.UniqueConstraint(
                fields=["transaction"],
                condition=Q(status="approved"),
                name="one_approved_match_per_txn",
            ),
        ]

    def __str__(self):
        return f"{self.transaction_id} -> {self.line_item_id} ({self.status})"