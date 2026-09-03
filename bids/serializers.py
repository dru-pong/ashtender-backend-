from rest_framework import serializers

from bids.models import ActionRequest, Bid, BidTeam, Interaction


class BidTeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = BidTeam
        fields = ("id", "bid", "partner_name", "partner_organization", "is_lead",
                  "share_pct", "classification_notation", "jv_agreement")


class BidSerializer(serializers.ModelSerializer):
    team_members = BidTeamSerializer(many=True, read_only=True)
    tender_title = serializers.CharField(source="tender.title", read_only=True)
    tender_closing_at = serializers.DateTimeField(source="tender.closing_at", read_only=True)
    verdict = serializers.CharField(source="latest_check.verdict", read_only=True, default=None)
    keeps_domestic_status = serializers.SerializerMethodField()

    class Meta:
        model = Bid
        fields = ("id", "tender", "tender_title", "tender_closing_at", "state",
                  "latest_check", "verdict", "go_decision_at", "go_decision_note",
                  "documents_purchased_on", "document_fee_paid", "submitted_at",
                  "submission_mode", "submission_evidence", "tender_price",
                  "currency", "priced_by", "foreign_subcontract_pct",
                  "keeps_domestic_status", "is_joint_venture", "outcome_note",
                  "won_contract", "team_members")

    def get_keeps_domestic_status(self, obj):
        """Tri-state: null when the subcontracting split is unrecorded."""
        return obj.keeps_domestic_status


class InteractionSerializer(serializers.ModelSerializer):
    entity_name = serializers.CharField(source="entity.name", read_only=True)

    class Meta:
        model = Interaction
        fields = ("id", "entity", "entity_name", "contact", "tender", "kind",
                  "occurred_at", "participants", "notes", "outcome",
                  "funding_source_learned", "payment_record_note", "next_action",
                  "next_action_at", "logged_by")
        read_only_fields = ("logged_by",)


class ActionRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionRequest
        fields = ("id", "kind", "status", "priority", "title", "brief", "due_at",
                  "tender", "bid", "finding", "document", "assigned_to",
                  "completed_at", "resolution_note")
