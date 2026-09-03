from rest_framework import serializers

from tenders.models import (
    Tender,
    TenderClassRequirement,
    TenderDiscipline,
    TenderRequirement,
    TenderSighting,
)


class TenderClassRequirementSerializer(serializers.ModelSerializer):
    notation = serializers.SerializerMethodField()
    scheme_code = serializers.CharField(source="scheme.code", read_only=True)

    class Meta:
        model = TenderClassRequirement
        fields = ("id", "scheme", "scheme_code", "category", "notation",
                  "min_financial_class", "or_better", "source_text", "origin")

    def get_notation(self, obj):
        return f"{obj.category.letter}{obj.min_financial_class}"


class TenderRequirementSerializer(serializers.ModelSerializer):
    spec_code = serializers.CharField(source="spec.code", read_only=True)
    spec_name = serializers.CharField(source="spec.name", read_only=True)

    class Meta:
        model = TenderRequirement
        fields = ("id", "spec", "spec_code", "spec_name", "origin", "tier",
                  "is_mandatory", "source_text", "confidence")


class TenderDisciplineSerializer(serializers.ModelSerializer):
    discipline_name = serializers.CharField(source="discipline.name", read_only=True)

    class Meta:
        model = TenderDiscipline
        fields = ("id", "discipline", "discipline_name", "confidence", "quantity", "unit")


class TenderSightingSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source="source.name", read_only=True)

    class Meta:
        model = TenderSighting
        fields = ("id", "source", "source_name", "first_seen_at", "url",
                  "is_first_sighting")


class TenderListSerializer(serializers.ModelSerializer):
    entity_name = serializers.CharField(source="entity.name", read_only=True)
    days_to_close = serializers.SerializerMethodField()
    my_verdict = serializers.SerializerMethodField()

    class Meta:
        model = Tender
        fields = ("id", "reference_number", "ocid", "title", "entity", "entity_name",
                  "object_type", "method", "funding_source", "estimated_value",
                  "currency", "value_is_estimated", "document_fee", "region",
                  "district", "published_at", "closing_at", "days_to_close",
                  "submission_mode", "is_cancelled", "my_verdict")

    def get_days_to_close(self, obj):
        from django.utils import timezone

        if obj.closing_at is None:
            return None
        return (obj.closing_at - timezone.now()).days

    def get_my_verdict(self, obj):
        """
        Populated from a per-request map the viewset builds in one query. A
        property that lazily hit the database here would be an N+1 on the
        busiest list in the product.
        """
        verdicts = self.context.get("verdict_by_tender_id") or {}
        return verdicts.get(obj.pk)


class TenderDetailSerializer(TenderListSerializer):
    required_classes = TenderClassRequirementSerializer(many=True, read_only=True)
    requirements = TenderRequirementSerializer(many=True, read_only=True)
    tender_disciplines = TenderDisciplineSerializer(many=True, read_only=True)
    sightings = TenderSightingSerializer(many=True, read_only=True)

    class Meta(TenderListSerializer.Meta):
        fields = TenderListSerializer.Meta.fields + (
            "description", "contract_form", "domain", "std_variant",
            "requires_tender_security", "tender_security_note",
            "prebid_meeting_at", "site_visit_at", "opening_at", "source_url",
            "required_classes", "requirements", "tender_disciplines", "sightings",
        )
