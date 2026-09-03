from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from bids.models import ActionRequest, Bid, BidTeam, Interaction
from bids.serializers import (
    ActionRequestSerializer,
    BidSerializer,
    BidTeamSerializer,
    InteractionSerializer,
)
from core.viewsets import TenantModelViewSet


class BidViewSet(TenantModelViewSet):
    queryset = Bid.objects.select_related(
        "tender", "tender__entity", "latest_check"
    ).prefetch_related("team_members")
    serializer_class = BidSerializer
    filterset_fields = ("state", "is_joint_venture", "tender")
    ordering_fields = ("created_at", "submitted_at", "tender__closing_at")


class BidTeamViewSet(TenantModelViewSet):
    queryset = BidTeam.objects.select_related("bid")
    serializer_class = BidTeamSerializer
    filterset_fields = ("bid", "is_lead")


class InteractionViewSet(TenantModelViewSet):
    """
    A tenant's relationship record.

    Never aggregated, never surfaced to another tenant, in any form. PayerScore
    reads IPC dates and does not touch this table -- there is a test asserting
    that.
    """

    queryset = Interaction.objects.select_related("entity", "contact", "tender")
    serializer_class = InteractionSerializer
    filterset_fields = ("entity", "kind", "tender")
    search_fields = ("notes", "outcome", "participants")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    ordering = ("-occurred_at",)

    def perform_create(self, serializer):
        serializer.save(
            organization_id=self.get_active_org_id(), logged_by=self.request.user
        )


class ActionRequestViewSet(TenantModelViewSet):
    """The work queue.  What a human needs to do, with the brief to do it."""

    queryset = ActionRequest.objects.select_related(
        "tender", "bid", "finding", "document", "assigned_to"
    )
    serializer_class = ActionRequestSerializer
    filterset_fields = ("kind", "status", "assigned_to", "tender", "bid")
    ordering = ("-priority", "due_at")
