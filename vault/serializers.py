from rest_framework import serializers

from vault.models import (
    Classification,
    Document,
    Equipment,
    ExtractedField,
    FinancialYear,
    KeyPersonnel,
    Litigation,
    PastProject,
)


class ExtractedFieldSerializer(serializers.ModelSerializer):
    bbox_is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = ExtractedField
        fields = ("id", "field_name", "value_raw", "value_date", "value_decimal",
                  "page", "bbox", "bbox_is_usable", "confidence", "confirmed_by_human")
        read_only_fields = ("bbox", "confidence")


class DocumentSerializer(serializers.ModelSerializer):
    """
    ``extracted`` and ``verified_by_human`` are both exposed because the
    difference matters to the caller: an extracted-but-unconfirmed date is
    enough to screen a tender in and not enough to submit a bid on.
    """

    spec_code = serializers.CharField(source="spec.code", read_only=True)
    extracted_fields = ExtractedFieldSerializer(many=True, read_only=True)

    class Meta:
        model = Document
        fields = ("id", "title", "file", "spec", "spec_code", "mime_type",
                  "page_count", "issued_on", "expires_on", "issuing_authority",
                  "reference_number", "content_hash", "extracted",
                  "verified_by_human", "verified_at", "superseded_by",
                  "extracted_fields", "created_at", "updated_at")
        read_only_fields = ("content_hash", "extracted", "verified_at", "page_count")


class ClassificationSerializer(serializers.ModelSerializer):
    notation = serializers.SerializerMethodField()
    scheme_code = serializers.CharField(source="scheme.code", read_only=True)
    category_letter = serializers.CharField(source="category.letter", read_only=True)
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = Classification
        fields = ("id", "scheme", "scheme_code", "category", "category_letter",
                  "financial_class", "notation", "certificate", "issued_on",
                  "valid_from", "valid_to", "is_current",
                  "verified_against_register_at", "register_entry", "notes")
        read_only_fields = ("verified_against_register_at", "register_entry")

    def get_notation(self, obj):
        return f"{obj.category.letter}{obj.financial_class}"

    def get_is_current(self, obj):
        """
        Tri-state: true, false, or null when no validity window is recorded.
        Null is not false, and collapsing them would report an unknown as a lapse.
        """
        from datetime import date

        return obj.is_current_on(date.today())


class FinancialYearSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = FinancialYear
        fields = ("id", "year", "period_end", "turnover", "currency",
                  "profit_before_tax", "net_worth", "working_capital",
                  "audited_accounts", "auditor_name", "is_complete")


class PastProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = PastProject
        fields = ("id", "name", "disciplines", "client_entity", "client_name",
                  "client_contact_name", "client_contact_phone", "client_contact_email",
                  "district", "value", "currency", "started_on", "completed_on",
                  "is_ongoing", "percent_complete", "completion_cert", "role")


class LitigationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Litigation
        fields = ("id", "year", "counterparty", "cause", "disputed_amount",
                  "currency", "outcome", "award_amount", "is_resolved", "evidence")


class EquipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Equipment
        fields = ("id", "name", "make_model", "year_of_manufacture", "quantity",
                  "capacity", "ownership", "registration_number", "condition", "proof")


class KeyPersonnelSerializer(serializers.ModelSerializer):
    class Meta:
        model = KeyPersonnel
        fields = ("id", "full_name", "position", "qualification", "professional_body",
                  "membership_number", "years_experience", "years_in_similar_works", "cv")
