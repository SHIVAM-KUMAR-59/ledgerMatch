from rest_framework.generics import ListAPIView

from recon.models import LineItem

from .scoping import OrgScopedMixin
from .serializers import LineItemSerializer


class LineItemListView(OrgScopedMixin, ListAPIView):
    serializer_class = LineItemSerializer

    def get_queryset(self):
        return (
            LineItem.objects.filter(organization=self.request.organization)
            .select_related("capital_call")
            .order_by("capital_call__number", "reference")
        )