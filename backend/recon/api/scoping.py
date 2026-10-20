from rest_framework.exceptions import NotFound, ParseError
from rest_framework.views import APIView

from recon.models import Organization


class OrgScopedMixin(APIView):
    """Resolve X-Organization into request.organization before any handler runs."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        slug = request.headers.get("X-Organization", "").strip()
        if not slug:
            raise ParseError("X-Organization header is required.")
        try:
            request.organization = Organization.objects.get(slug=slug)
        except Organization.DoesNotExist:
            raise NotFound("Unknown organization.")