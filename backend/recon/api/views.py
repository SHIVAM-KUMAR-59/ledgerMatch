from django.db.models import Prefetch
from rest_framework.exceptions import ParseError
from rest_framework.generics import ListAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from recon.models import BankTransaction, LineItem, TransactionMatch
from recon.services import reconciliation
from recon.services.airtable_sync import sync_after_import
from recon.services.csv_import import import_statement
from recon.services.csv_parser import FileRejected

from .errors import translate_service_errors
from .scoping import OrgScopedMixin
from .serializers import BankTransactionSerializer, LineItemSerializer

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def transactions_for(organization):
    matches = TransactionMatch.objects.select_related("line_item").order_by("id")
    return BankTransaction.objects.filter(organization=organization).prefetch_related(
        Prefetch("matches", queryset=matches)
    )


def transaction_response(organization, transaction_id):
    txn = transactions_for(organization).get(pk=transaction_id)
    return Response(BankTransactionSerializer(txn).data)


class LineItemListView(OrgScopedMixin, ListAPIView):
    serializer_class = LineItemSerializer

    def get_queryset(self):
        return (
            LineItem.objects.filter(organization=self.request.organization)
            .select_related("capital_call")
            .order_by("capital_call__number", "reference")
        )


class TransactionListView(OrgScopedMixin, ListAPIView):
    serializer_class = BankTransactionSerializer

    def get_queryset(self):
        return transactions_for(self.request.organization)


class TransactionImportView(OrgScopedMixin, APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ParseError(
                "No file uploaded. Send it as the multipart form field 'file'."
            )
        if upload.size > MAX_UPLOAD_BYTES:
            raise ParseError("The file is larger than 10 MB.")
        try:
            summary = import_statement(request.organization, upload.read())
        except FileRejected as exc:
            raise ParseError(str(exc)) from None
        # The import has committed by now. The optional Airtable sync runs after
        # it and can never undo it (sync_after_import does not raise).
        summary["airtable"] = sync_after_import(request.organization)
        return Response(summary)


class MatchApproveView(OrgScopedMixin, APIView):
    def post(self, request, match_id):
        with translate_service_errors():
            txn = reconciliation.approve_match(request.organization, match_id)
        return transaction_response(request.organization, txn.pk)


class MatchRejectView(OrgScopedMixin, APIView):
    def post(self, request, match_id):
        with translate_service_errors():
            txn = reconciliation.reject_match(request.organization, match_id)
        return transaction_response(request.organization, txn.pk)


class TransactionIgnoreView(OrgScopedMixin, APIView):
    def post(self, request, transaction_id):
        with translate_service_errors():
            txn = reconciliation.ignore_transaction(
                request.organization, transaction_id
            )
        return transaction_response(request.organization, txn.pk)