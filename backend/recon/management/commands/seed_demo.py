from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from recon.models import CapitalCall, LineItem, Organization

ORGS = [
    ("northwind", "Northwind Ventures"),
    ("bluebird", "Bluebird Capital"),
]

# (org slug, call number, call date, due date)
CALLS = [
    ("northwind", 1, date(2026, 3, 25), date(2026, 4, 10)),
    ("northwind", 2, date(2026, 4, 1), date(2026, 4, 20)),
    ("bluebird", 1, date(2026, 4, 1), date(2026, 4, 15)),
]

# (org slug, call number, investor entity, reference, amount called)
LINE_ITEMS = [
    ("northwind", 1, "Haller Capital LLC", "CC-1001", "27500.00"),
    ("northwind", 1, "Rahul Shah Trust", "CC-1002", "25000.00"),
    ("northwind", 1, "Patel Family Trust", "CC-1003", "12500.00"),
    ("northwind", 1, "Meridian Partners LP", "CC-1004", "15000.00"),
    ("northwind", 1, "Oakridge Holdings LLC", "CC-1005", "15000.00"),
    ("northwind", 2, "Oakridge Holdings LLC", "CC-1006", "15000.00"),
    ("bluebird", 1, "Cedar Grove Fund LP", "CC-2001", "40000.00"),
]


class Command(BaseCommand):
    help = "Create demo organizations, capital calls and line items (safe to re-run)."

    @transaction.atomic
    def handle(self, *args, **options):
        orgs = {}
        for slug, name in ORGS:
            orgs[slug], _ = Organization.objects.get_or_create(
                slug=slug, defaults={"name": name}
            )

        calls = {}
        for slug, number, call_date, due_date in CALLS:
            calls[(slug, number)], _ = CapitalCall.objects.get_or_create(
                organization=orgs[slug],
                number=number,
                defaults={"call_date": call_date, "due_date": due_date},
            )

        created = 0
        for slug, number, investor, reference, amount in LINE_ITEMS:
            _, was_created = LineItem.objects.get_or_create(
                organization=orgs[slug],
                reference=reference,
                defaults={
                    "capital_call": calls[(slug, number)],
                    "investor_name": investor,
                    "amount_called": Decimal(amount),
                },
            )
            if was_created:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {created} new line items "
                f"({len(LINE_ITEMS)} defined)."
            )
        )