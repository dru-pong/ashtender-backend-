from rest_framework import serializers

from eligibility.models import EligibilityCheck, RequirementFinding


class RequirementFindingSerializer(serializers.ModelSerializer):
    """
    ``source_text`` and ``satisfied_by`` are always serialised, even when
    empty, so a client can render "we could not quote a source for this" rather
    than presenting an unsourced verdict as though it were sourced.
    """

    spec_code = serializers.CharField(source="spec.code", read_only=True)
    spec_name = serializers.CharField(source="spec.name", read_only=True)
    satisfied_by_title = serializers.CharField(
        source="satisfied_by.title", read_only=True, default=None
    )

    class Meta:
        model = RequirementFinding
        fields = ("id", "code", "verdict", "spec", "spec_code", "spec_name",
                  "is_mandatory", "reason", "source_text", "satisfied_by",
                  "satisfied_by_title", "satisfied_by_classification",
                  "confidence", "remedy")


class EligibilityCheckSerializer(serializers.ModelSerializer):
    findings = RequirementFindingSerializer(many=True, read_only=True)
    tender_title = serializers.CharField(source="tender.title", read_only=True)
    tender_reference = serializers.CharField(source="tender.reference_number", read_only=True)
    blocking_count = serializers.SerializerMethodField()

    class Meta:
        model = EligibilityCheck
        fields = ("id", "tender", "tender_reference", "tender_title", "verdict",
                  "summary", "engine_version", "screened_out_reason",
                  "assessed_against_closing_at", "computed_at", "inputs_digest",
                  "blocking_count", "findings")

    def get_blocking_count(self, obj):
        from core.enums import BLOCKING_VERDICTS

        return sum(1 for f in obj.findings.all() if f.verdict in BLOCKING_VERDICTS)


class EligibilitySummarySerializer(serializers.ModelSerializer):
    """List view: the verdict without dragging every finding along."""

    tender_title = serializers.CharField(source="tender.title", read_only=True)

    class Meta:
        model = EligibilityCheck
        fields = ("id", "tender", "tender_title", "verdict", "summary",
                  "screened_out_reason", "computed_at")
