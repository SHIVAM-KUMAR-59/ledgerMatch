from contextlib import contextmanager

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound

from recon.services.reconciliation import (
    ReconciliationConflict,
    ReconciliationNotFound,
)


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflict."
    default_code = "conflict"


@contextmanager
def translate_service_errors():
    """Map service exceptions to 404 and 409 with DRF's usual {"detail": ...}."""
    try:
        yield
    except ReconciliationNotFound as exc:
        raise NotFound(str(exc)) from None
    except ReconciliationConflict as exc:
        raise Conflict(str(exc)) from None