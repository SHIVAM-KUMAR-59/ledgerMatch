from django.core.management.base import BaseCommand, CommandError

from recon.integrations.airtable import build_airtable_client
from recon.models import BankTransaction, Organization
from recon.services.airtable_sync import sync_needs_review


class Command(BaseCommand):
    help = "Send not-yet-synced needs_review transactions to Airtable."

    def add_arguments(self, parser):
        parser.add_argument("--org", help="Only this organization (slug).")
        parser.add_argument(
            "--limit", type=int, help="Maximum rows to send per organization."
        )
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help=(
                "Also re-send rows marked sync_failed. If an earlier request "
                "timed out after Airtable created the record, this can create "
                "a duplicate."
            ),
        )

    def handle(self, *args, **options):
        client = build_airtable_client()
        if client is None:
            raise CommandError(
                "Airtable is not configured. Set AIRTABLE_TOKEN, AIRTABLE_BASE_ID "
                "and AIRTABLE_TABLE in .env."
            )

        organizations = Organization.objects.order_by("slug")
        if options["org"]:
            organizations = organizations.filter(slug=options["org"])
            if not organizations.exists():
                raise CommandError(f"Unknown organization '{options['org']}'.")

        for organization in organizations:
            if options["retry_failed"]:
                BankTransaction.objects.filter(
                    organization=organization,
                    status=BankTransaction.Status.NEEDS_REVIEW,
                    sync_status=BankTransaction.SyncStatus.SYNC_FAILED,
                ).update(
                    sync_status=BankTransaction.SyncStatus.NOT_SYNCED, sync_error=""
                )
            result = sync_needs_review(client, organization, limit=options["limit"])
            line = (
                f"{organization.slug}: {result['synced']} synced, "
                f"{result['failed']} failed, {result['remaining']} waiting"
            )
            if result["error"]:
                line += f" (error: {result['error']})"
            self.stdout.write(line)