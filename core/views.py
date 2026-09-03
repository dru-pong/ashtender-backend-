"""Health endpoint.  Phase 0 acceptance: this returns 200 on a fresh clone."""
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from eligibility.rules import ENGINE_VERSION


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """
    Liveness plus the two facts that matter when something is wrong: which
    engine version is deciding verdicts, and whether the database is reachable.
    """
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - only fires on a real outage
        checks["database"] = f"error: {type(exc).__name__}"

    healthy = all(value == "ok" for value in checks.values())
    return Response(
        {
            "status": "ok" if healthy else "degraded",
            "engine_version": ENGINE_VERSION,
            "database_vendor": connection.vendor,
            "checks": checks,
        },
        status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
