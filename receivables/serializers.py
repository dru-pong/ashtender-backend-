from rest_framework import serializers

from receivables.models import IPC, ArrearsClaim, Contract, PayerScore


class IPCSerializer(serializers.ModelSerializer):
    days_to_certify = serializers.IntegerField(read_only=True)
    days_to_pay = serializers.IntegerField(read_only=True)
    days_outstanding = serializers.SerializerMethodField()
    amount_outstanding = serializers.DecimalField(
        max_digits=18, decimal_places=2, read_only=True
    )

    class Meta:
        model = IPC
        fields = ("id", "contract", "number", "period_start", "period_end",
                  "amount_claimed", "amount_certified", "amount_paid", "currency",
                  "retention_held", "prepared_on", "submitted_on", "certified_on",
                  "validated_on", "paid_on", "status", "rejection_reason",
                  "days_to_certify", "days_to_pay", "days_outstanding",
                  "amount_outstanding", "notes")

    def get_days_outstanding(self, obj):
        return obj.days_outstanding()


class ContractSerializer(serializers.ModelSerializer):
    entity_name = serializers.CharField(source="entity.name", read_only=True)
    total_certified = serializers.DecimalField(
        max_digits=18, decimal_places=2, read_only=True
    )
    total_paid = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = Contract
        fields = ("id", "name", "entity", "entity_name", "tender", "contract_number",
                  "ppa_reference", "funding_source", "value", "currency",
                  "award_date", "commencement_date", "completion_date",
                  "duration_months", "retention_pct", "defects_liability_months",
                  "district", "is_complete", "total_certified", "total_paid",
                  "outstanding", "notes")


class ArrearsClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArrearsClaim
        fields = ("id", "reference", "exercise_name", "ipcs", "submitted_on",
                  "amount_submitted", "amount_validated", "outcome", "outcome_on",
                  "rejection_reason")


class PayerScoreSerializer(serializers.ModelSerializer):
    """
    Read-only and publishable-only.  ``sample_contracts`` is exposed so a
    client can show how thin the evidence is rather than presenting a median
    over five contracts with the same confidence as one over five hundred.
    """

    entity_name = serializers.CharField(source="entity.name", read_only=True)
    sample_contracts = serializers.IntegerField(source="contract_count", read_only=True)

    class Meta:
        model = PayerScore
        fields = ("id", "entity", "entity_name", "funding_source",
                  "median_days_to_certify", "median_days_to_pay", "p90_days_to_pay",
                  "rejection_rate", "sample_contracts", "computed_at")
