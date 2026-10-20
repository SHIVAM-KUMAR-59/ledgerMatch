from rest_framework.exceptions import ParseError
from rest_framework.generics import ListAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from recon.models import LineItem
from recon.services.csv_import import import_statement
from recon.services.csv_parser import FileRejected

from .scoping import OrgScopedMixin
from .serializers import LineItemSerializer

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class LineItemListView(OrgScopedMixin, ListAPIView):
    serializer_class = LineItemSerializer

    def get_queryset(self):
        return (
            LineItem.objects.filter(organization=self.request.organization)
            .select_related("capital_call")
            .order_by("capital_call__number", "reference")
        )


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
        return Response(summary)