from rest_framework import serializers

from catalog.models import (
    ClassificationScheme,
    CredentialAuthority,
    Discipline,
    District,
    FinancialClassCap,
    ProcurementDomain,
    ProcuringEntity,
    Region,
    RequirementSpec,
    WorkCategory,
)


class RegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = ("id", "code", "name", "capital", "district_seed_complete")


class DistrictSerializer(serializers.ModelSerializer):
    region_code = serializers.CharField(source="region.code", read_only=True)

    class Meta:
        model = District
        fields = ("id", "name", "kind", "capital", "region", "region_code")


class WorkCategorySerializer(serializers.ModelSerializer):
    scheme_code = serializers.CharField(source="scheme.code", read_only=True)

    class Meta:
        model = WorkCategory
        fields = ("id", "scheme", "scheme_code", "letter", "name",
                  "max_financial_class", "object_type")


class ClassificationSchemeSerializer(serializers.ModelSerializer):
    categories = WorkCategorySerializer(many=True, read_only=True)

    class Meta:
        model = ClassificationScheme
        fields = ("id", "code", "name", "register_url", "register_ingestible",
                  "publication_cadence", "notes", "categories")


class FinancialClassCapSerializer(serializers.ModelSerializer):
    """
    ``is_stale`` and ``source_note`` are serialised deliberately: a client
    showing a ceiling figure should be able to show that nobody has confirmed it.
    """

    scheme_code = serializers.CharField(source="scheme.code", read_only=True)

    class Meta:
        model = FinancialClassCap
        fields = ("id", "scheme", "scheme_code", "category", "financial_class",
                  "amount", "currency", "effective_from", "effective_to",
                  "is_stale", "source_note")


class RequirementSpecSerializer(serializers.ModelSerializer):
    authority_name = serializers.CharField(source="authority.name", read_only=True)

    class Meta:
        model = RequirementSpec
        fields = ("id", "code", "name", "doc_type", "authority", "authority_name",
                  "validity_mode", "recency_months", "verification_method",
                  "statutory_reference", "is_conditional", "condition_note")


class CredentialAuthoritySerializer(serializers.ModelSerializer):
    class Meta:
        model = CredentialAuthority
        fields = ("id", "code", "name", "register_url", "register_ingestible",
                  "contact_phone", "contact_address")


class ProcurementDomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcurementDomain
        fields = ("id", "code", "name", "object_type", "description")


class DisciplineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Discipline
        fields = ("id", "slug", "name", "domain", "ocds_title_count", "depth")


class ProcuringEntitySerializer(serializers.ModelSerializer):
    """
    The entity dossier.  PayerScore is attached only where it passed the
    minimum-n floor, so an unpublishable aggregate is absent rather than
    rendered with a caveat nobody reads.
    """

    payer_scores = serializers.SerializerMethodField()
    recent_awards = serializers.SerializerMethodField()
    contacts = serializers.SerializerMethodField()

    class Meta:
        model = ProcuringEntity
        fields = ("id", "name", "slug", "channel", "entity_category", "region",
                  "district", "parent_ministry", "website", "ocds_party_id",
                  "contacts", "payer_scores", "recent_awards")

    def get_contacts(self, obj):
        return [
            {
                "full_name": contact.full_name,
                "title": contact.title,
                "phones": contact.phones,
                "email": contact.email,
                "source_tier": contact.source_tier,
                "verified_by_human": contact.verified_by_human,
            }
            for contact in obj.contacts.all()[:20]
        ]

    def get_payer_scores(self, obj):
        return [
            {
                "funding_source": score.funding_source,
                "median_days_to_certify": score.median_days_to_certify,
                "median_days_to_pay": score.median_days_to_pay,
                "p90_days_to_pay": score.p90_days_to_pay,
                "rejection_rate": score.rejection_rate,
                "sample_contracts": score.contract_count,
            }
            for score in obj.payer_scores.filter(is_publishable=True)
        ]

    def get_recent_awards(self, obj):
        return [
            {
                "winner_name": award.winner_name,
                "value": award.value,
                "currency": award.currency,
                "award_date": award.award_date,
                "bidder_count": award.bidder_count,
                "title": award.title[:200],
            }
            for award in obj.awards.all()[:10]
        ]
