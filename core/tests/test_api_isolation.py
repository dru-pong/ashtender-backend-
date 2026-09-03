"""
API-level isolation tests.

The queryset guard catches a forgotten scope at evaluation time. These tests
check the layer above it: that a real authenticated request for one tenant
cannot reach another tenant's rows by changing the URL, the header, or the pk.
"""
from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from core.tests.factories import make_document, make_org, make_spec, make_user

HEADER = "X-AshTender-Org"


class ApiIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.spec = make_spec()
        cls.org_a = make_org("Alpha Works Ltd", "alpha")
        cls.org_b = make_org("Beta Civils Ltd", "beta")
        cls.user_a = make_user("alice", cls.org_a)
        cls.user_b = make_user("bob", cls.org_b)
        cls.doc_a = make_document(cls.org_a, cls.spec, title="Alpha TCC")
        cls.doc_b = make_document(cls.org_b, cls.spec, title="Beta TCC")

    def setUp(self):
        self.client = APIClient()

    def as_user(self, user, org):
        self.client.force_authenticate(user=user)
        self.client.credentials(**{f"HTTP_{HEADER.upper().replace('-', '_')}": str(org.pk)})

    def test_a_tenant_sees_only_its_own_documents(self):
        self.as_user(self.user_a, self.org_a)
        response = self.client.get(f"/api/v1/orgs/{self.org_a.pk}/documents/")
        self.assertEqual(response.status_code, 200)
        titles = [row["title"] for row in response.json()["results"]]
        self.assertEqual(titles, ["Alpha TCC"])

    def test_requesting_another_orgs_collection_is_refused(self):
        """Changing the org in the path is not a way in."""
        self.as_user(self.user_a, self.org_a)
        response = self.client.get(f"/api/v1/orgs/{self.org_b.pk}/documents/")
        self.assertEqual(response.status_code, 403)

    def test_another_tenants_document_is_not_found_by_pk(self):
        """
        404, not 403: whether a given pk exists in another tenant's vault is
        itself information, and a 403 would confirm it.
        """
        self.as_user(self.user_a, self.org_a)
        response = self.client.get(
            f"/api/v1/orgs/{self.org_a.pk}/documents/{self.doc_b.pk}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_claiming_membership_of_an_org_you_do_not_belong_to_is_refused(self):
        """The header is checked against real memberships, not trusted."""
        self.client.force_authenticate(user=self.user_a)
        self.client.credentials(HTTP_X_ASHTENDER_ORG=str(self.org_b.pk))
        response = self.client.get(f"/api/v1/orgs/{self.org_b.pk}/documents/")
        self.assertEqual(response.status_code, 403)

    def test_no_header_and_no_membership_context_is_refused(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(f"/api/v1/orgs/{self.org_b.pk}/documents/")
        self.assertEqual(response.status_code, 403)

    def test_anonymous_access_is_refused(self):
        response = self.client.get(f"/api/v1/orgs/{self.org_a.pk}/documents/")
        self.assertIn(response.status_code, (401, 403))

    def test_a_single_membership_resolves_without_a_header(self):
        """Convenience for the common case; never a guess when there are two."""
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(f"/api/v1/orgs/{self.org_a.pk}/documents/")
        self.assertEqual(response.status_code, 200)

    def test_the_url_disambiguates_a_user_with_two_memberships(self):
        """
        A user in two organizations is fine as long as the request says which
        one. The URL saying it is enough -- that is what the org segment is for.
        """
        from tenancy.models import Membership

        Membership.objects.create(
            organization=self.org_b, user=self.user_a, role=Membership.Role.VIEWER
        )
        self.client.force_authenticate(user=self.user_a)
        for org in (self.org_a, self.org_b):
            with self.subTest(org=org.slug):
                response = self.client.get(f"/api/v1/orgs/{org.pk}/documents/")
                self.assertEqual(response.status_code, 200)

    def test_a_header_contradicting_the_url_is_refused(self):
        """
        A client naming one organization in the path and another in the header
        is confused or hostile. Either way, picking one would be a guess.
        """
        from tenancy.models import Membership

        Membership.objects.create(
            organization=self.org_b, user=self.user_a, role=Membership.Role.VIEWER
        )
        self.client.force_authenticate(user=self.user_a)
        self.client.credentials(HTTP_X_ASHTENDER_ORG=str(self.org_b.pk))
        response = self.client.get(f"/api/v1/orgs/{self.org_a.pk}/documents/")
        self.assertEqual(response.status_code, 403)

    def test_a_global_endpoint_gets_no_implicit_default(self):
        """
        Where no organization is named anywhere, a user with two memberships
        resolves to none. Guessing which competitor they meant is the one
        mistake this system cannot make.
        """
        from tenancy.models import Membership

        Membership.objects.create(
            organization=self.org_b, user=self.user_a, role=Membership.Role.VIEWER
        )
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/api/v1/tenders/", {"eligible_for_me": "true"})
        self.assertEqual(response.status_code, 403)

    def test_created_rows_are_stamped_with_the_active_org_not_the_payload(self):
        """A client cannot write a row into another tenant's vault."""
        self.as_user(self.user_a, self.org_a)
        response = self.client.post(
            f"/api/v1/orgs/{self.org_a.pk}/financial-years/",
            {"year": 2025, "turnover": "500000", "organization": self.org_b.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        from vault.models import FinancialYear

        created = FinancialYear.objects.for_org(self.org_a.pk).get(year=2025)
        self.assertEqual(created.organization_id, self.org_a.pk)

    def test_confirming_a_document_records_who_looked(self):
        self.as_user(self.user_a, self.org_a)
        response = self.client.post(
            f"/api/v1/orgs/{self.org_a.pk}/documents/{self.doc_a.pk}/confirm/"
        )
        self.assertEqual(response.status_code, 200)
        self.doc_a.refresh_from_db()
        self.assertTrue(self.doc_a.verified_by_human)
        self.assertEqual(self.doc_a.verified_by_id, self.user_a.pk)

    def test_another_tenants_document_cannot_be_confirmed(self):
        self.as_user(self.user_a, self.org_a)
        response = self.client.post(
            f"/api/v1/orgs/{self.org_a.pk}/documents/{self.doc_b.pk}/confirm/"
        )
        self.assertEqual(response.status_code, 404)
        self.doc_b.refresh_from_db()
        self.assertFalse(self.doc_b.verified_by_human)


class HealthEndpointTests(TestCase):
    """Phase 0 acceptance: health returns 200 on a fresh clone, unauthenticated."""

    def test_health_is_public_and_reports_the_engine_version(self):
        from eligibility.rules import ENGINE_VERSION

        response = APIClient().get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["engine_version"], ENGINE_VERSION)
        self.assertEqual(payload["checks"]["database"], "ok")


class GlobalEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.tests.factories import make_entity, make_tender

        cls.org = make_org("Alpha Works Ltd", "alpha")
        cls.user = make_user("alice", cls.org)
        cls.entity = make_entity()
        cls.tender = make_tender(
            cls.entity, reference="MRH/RF/2026/014",
            title="Upgrading of Selected Roads in Bechem Town",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_tenders_are_visible_to_every_tenant(self):
        response = self.client.get("/api/v1/tenders/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

    def test_search_finds_a_punctuation_heavy_reference(self):
        """
        Tender references are punctuation-heavy and get mangled by full-text
        tokenisation, so substring matching runs alongside it.
        """
        response = self.client.get("/api/v1/tenders/search/", {"q": "MRH/RF/2026/014"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_search_finds_a_title_phrase(self):
        response = self.client.get("/api/v1/tenders/search/", {"q": "Bechem"})
        self.assertEqual(len(response.json()["results"]), 1)

    def test_search_with_no_match_is_empty_not_an_error(self):
        response = self.client.get("/api/v1/tenders/search/", {"q": "submarine"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_eligible_for_me_needs_an_active_organization(self):
        from tenancy.models import Membership

        other = make_org("Beta Civils Ltd", "beta")
        Membership.objects.create(
            organization=other, user=self.user, role=Membership.Role.VIEWER
        )
        response = self.client.get("/api/v1/tenders/", {"eligible_for_me": "true"})
        self.assertEqual(response.status_code, 403)

    def test_eligible_for_me_shows_only_passing_tenders(self):
        from core.enums import Verdict
        from core.tests.factories import make_tender
        from eligibility.models import EligibilityCheck
        from django.utils import timezone

        unknown_tender = make_tender(self.entity, reference="MRH/RF/2026/015")
        EligibilityCheck.objects.create(
            organization=self.org, tender=self.tender, computed_at=timezone.now(),
            verdict=Verdict.PASS, engine_version="0.1.0",
        )
        EligibilityCheck.objects.create(
            organization=self.org, tender=unknown_tender, computed_at=timezone.now(),
            verdict=Verdict.UNKNOWN, engine_version="0.1.0",
        )
        self.client.credentials(HTTP_X_ASHTENDER_ORG=str(self.org.pk))
        response = self.client.get("/api/v1/tenders/", {"eligible_for_me": "true"})
        self.assertEqual(response.status_code, 200)
        references = [row["reference_number"] for row in response.json()["results"]]
        self.assertEqual(references, ["MRH/RF/2026/014"])

    def test_the_newest_check_wins_when_a_tender_was_re_evaluated(self):
        """
        Checks are immutable, so a tender accumulates them. The list must
        reflect the latest, not whichever row the database returned first.
        """
        from datetime import timedelta

        from core.enums import Verdict
        from eligibility.models import EligibilityCheck
        from django.utils import timezone

        now = timezone.now()
        EligibilityCheck.objects.create(
            organization=self.org, tender=self.tender,
            computed_at=now - timedelta(days=2), verdict=Verdict.PASS,
            engine_version="0.1.0",
        )
        EligibilityCheck.objects.create(
            organization=self.org, tender=self.tender, computed_at=now,
            verdict=Verdict.FAIL, engine_version="0.1.0",
        )
        self.client.credentials(HTTP_X_ASHTENDER_ORG=str(self.org.pk))
        response = self.client.get("/api/v1/tenders/", {"eligible_for_me": "true"})
        self.assertEqual(response.json()["results"], [])

    def test_my_verdict_costs_the_same_number_of_queries_for_one_row_or_many(self):
        """
        The real invariant is constant queries, not a magic number. Comparing
        one tender against three catches a regression to a per-row lookup,
        which an exact count on a single row cannot.
        """
        from core.enums import Verdict
        from core.tests.factories import make_tender
        from eligibility.models import EligibilityCheck
        from django.utils import timezone

        EligibilityCheck.objects.create(
            organization=self.org, tender=self.tender, computed_at=timezone.now(),
            verdict=Verdict.UNKNOWN, engine_version="0.1.0",
        )
        self.client.credentials(HTTP_X_ASHTENDER_ORG=str(self.org.pk))

        # membership lookup, page count, tender page, one verdict map
        with self.assertNumQueries(4):
            response = self.client.get("/api/v1/tenders/")
        self.assertEqual(response.json()["results"][0]["my_verdict"], "UNKNOWN")

        for index in range(2, 5):
            extra = make_tender(self.entity, reference=f"MRH/RF/2026/{index:03d}")
            EligibilityCheck.objects.create(
                organization=self.org, tender=extra, computed_at=timezone.now(),
                verdict=Verdict.PASS, engine_version="0.1.0",
            )
        with self.assertNumQueries(4):
            response = self.client.get("/api/v1/tenders/")
        self.assertEqual(response.json()["count"], 4)
        self.assertEqual(
            sorted(row["my_verdict"] for row in response.json()["results"]),
            ["PASS", "PASS", "PASS", "UNKNOWN"],
        )


class PayerScorePublicationTests(TestCase):
    """An unpublishable aggregate must be unreachable, not merely unrendered."""

    @classmethod
    def setUpTestData(cls):
        from core.enums import FundingSource
        from core.tests.factories import make_entity
        from django.utils import timezone
        from receivables.models import PayerScore

        cls.org = make_org("Alpha Works Ltd", "alpha")
        cls.user = make_user("alice", cls.org)
        cls.entity = make_entity()
        cls.hidden = PayerScore.objects.create(
            entity=cls.entity, funding_source=FundingSource.GOG,
            median_days_to_pay=300, contract_count=2, distinct_tenant_count=1,
            is_publishable=False, computed_at=timezone.now(),
        )
        cls.shown = PayerScore.objects.create(
            entity=cls.entity, funding_source=FundingSource.IFI,
            median_days_to_pay=45, contract_count=9, distinct_tenant_count=4,
            is_publishable=True, computed_at=timezone.now(),
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_only_publishable_scores_are_listed(self):
        response = self.client.get("/api/v1/payer-scores/")
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()["results"]]
        self.assertEqual(ids, [self.shown.pk])

    def test_an_unpublishable_score_is_not_reachable_by_pk(self):
        response = self.client.get(f"/api/v1/payer-scores/{self.hidden.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_the_entity_dossier_omits_unpublishable_scores(self):
        response = self.client.get(f"/api/v1/entities/{self.entity.pk}/")
        self.assertEqual(response.status_code, 200)
        sources = [row["funding_source"] for row in response.json()["payer_scores"]]
        self.assertEqual(sources, ["IFI"])
