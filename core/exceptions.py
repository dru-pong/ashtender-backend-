import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from core.tenancy import NoActiveOrganization, TenantScopeMissing

logger = logging.getLogger("core.tenancy")


def ashtender_exception_handler(exc, context):
    """
    A missing tenant scope is never a 4xx.

    It is a server-side defect with data-breach potential, so it is logged at
    exception level and returned as a 500 with no detail.  Returning 403 would
    invite someone to treat it as a permissions edge case and retry.
    """
    if isinstance(exc, TenantScopeMissing):
        logger.exception("tenant scope missing in %s", context.get("view"))
        return Response(
            {"detail": "Internal error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    if isinstance(exc, NoActiveOrganization):
        return Response(
            {
                "detail": "No active organization. Send the X-AshTender-Org header.",
                "code": "no_active_organization",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return exception_handler(exc, context)
