"""
Tests for the isolation guarantee.

If any test in this file fails, the correct response is to stop shipping.  The
failure mode being guarded against is one tenant reading another's documents,
bid intentions or payment history, and the tenants are direct competitors
bidding the same tenders.
"""
from django.test import TestCase, override_settings

from core.tenancy import (
    NoActiveOrganization,
    TenantScopeMissing,
    active_org,
    get_active_org_id,
)
from core.tests.factories import make_document, make_org, make_spec
from vault.models import Document


class UnscopedQuerysetRaisesTests(TestCase):
    """Phase 1 acceptance: the guard raises on an unfiltered TenantOwned queryset."""

    @classmethod
    def setUpTestData(cls):
        cls.spec = make_spec()
        cls.org_a = make_org("Alpha Works Ltd", "alpha")
        cls.org_b = make_org("Beta Civils Ltd", "beta")
        make_document(cls.org_a, cls.spec, title="Alpha TCC")
        make_document(cls.org_b, cls.spec, title="Beta TCC")

    def test_all_without_scope_raises(self):
        with self.assertRaises(TenantScopeMissing):
            list(Document.objects.all())

    def test_filter_on_non_tenant_field_still_raises(self):
        """Filtering by something else does not count as scoping."""
        with self.assertRaises(TenantScopeMissing):
            list(Document.objects.filter(title__icontains="TCC"))

    def test_count_raises(self):
        """An unscoped count leaks how many documents other tenants hold."""
        with self.assertRaises(TenantScopeMissing):
            Document.objects.count()

    def test_exists_raises(self):
        with self.assertRaises(TenantScopeMissing):
            Document.objects.exists()

    def test_aggregate_raises(self):
        from django.db.models import Count

        with self.assertRaises(TenantScopeMissing):
            Document.objects.aggregate(n=Count("id"))

    def test_update_raises(self):
        with self.assertRaises(TenantScopeMissing):
            Document.objects.update(title="overwritten")

    def test_delete_raises(self):
        with self.assertRaises(TenantScopeMissing):
            Document.objects.delete()

    def test_first_raises(self):
        with self.assertRaises(TenantScopeMissing):
            Document.objects.first()


class ScopedQuerysetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.spec = make_spec()
        cls.org_a = make_org("Alpha Works Ltd", "alpha")
        cls.org_b = make_org("Beta Civils Ltd", "beta")
        cls.doc_a = make_document(cls.org_a, cls.spec, title="Alpha TCC")
        cls.doc_b = make_document(cls.org_b, cls.spec, title="Beta TCC")

    def test_for_org_returns_only_that_org(self):
        titles = list(Document.objects.for_org(self.org_a).values_list("title", flat=True))
        self.assertEqual(titles, ["Alpha TCC"])

    def test_for_org_accepts_pk(self):
        self.assertEqual(Document.objects.for_org(self.org_b.pk).count(), 1)

    def test_scope_survives_further_chaining(self):
        """The flag must survive filter, exclude, order_by and slicing."""
        qs = (
            Document.objects.for_org(self.org_a)
            .filter(title__icontains="tcc")
            .exclude(title="nothing")
            .order_by("-created_at")[:10]
        )
        self.assertEqual([d.title for d in qs], ["Alpha TCC"])

    def test_explicit_organization_filter_counts_as_scoping(self):
        """This is what makes reverse relations from Organization work."""
        self.assertEqual(Document.objects.filter(organization=self.org_a).count(), 1)

    def test_reverse_relation_from_organization_does_not_raise(self):
        self.assertEqual(self.org_a.vault_document_set.count(), 1)

    def test_for_parent_inherits_the_parents_org(self):
        from vault.models import DocumentPage

        DocumentPage.objects.create(
            organization=self.org_a, document=self.doc_a, page_number=1, image="p.png"
        )
        pages = DocumentPage.objects.for_parent(self.doc_a)
        self.assertEqual(pages.count(), 1)

    def test_filter_on_a_tenant_owned_parent_counts_as_scoping(self):
        """
        A child row's tenant is fixed by its parent's, so filtering on the
        parent FK pins the queryset to one tenant.  This is what lets
        ``document.pages``, ``contract.ipcs`` and ``check.findings`` work
        without every caller repeating .for_org().
        """
        from vault.models import DocumentPage

        DocumentPage.objects.create(
            organization=self.org_a, document=self.doc_a, page_number=1, image="p.png"
        )
        DocumentPage.objects.create(
            organization=self.org_b, document=self.doc_b, page_number=1, image="p.png"
        )
        self.assertEqual(DocumentPage.objects.filter(document=self.doc_a).count(), 1)

    def test_reverse_relation_through_a_tenant_owned_parent_works(self):
        from vault.models import DocumentPage

        DocumentPage.objects.create(
            organization=self.org_a, document=self.doc_a, page_number=1, image="p.png"
        )
        self.assertEqual(self.doc_a.pages.count(), 1)

    def test_traversal_through_a_parent_does_not_count_as_scoping(self):
        """
        ``filter(document__title=...)`` spans every tenant whose document
        happens to share a title, so it is not a scope.  Only equality on the
        FK itself counts.
        """
        from vault.models import DocumentPage

        with self.assertRaises(TenantScopeMissing):
            list(DocumentPage.objects.filter(document__title="Alpha TCC"))

    def test_filter_on_a_parent_id_list_does_not_count_as_scoping(self):
        from vault.models import DocumentPage

        with self.assertRaises(TenantScopeMissing):
            list(DocumentPage.objects.filter(document_id__in=[self.doc_a.pk, self.doc_b.pk]))

    def test_filter_on_a_non_tenant_owned_parent_does_not_count_as_scoping(self):
        """
        RequirementSpec is global reference data, so filtering documents by
        spec says nothing about which tenant owns them.
        """
        with self.assertRaises(TenantScopeMissing):
            list(Document.objects.filter(spec=self.spec))

    def test_exclude_alone_does_not_count_as_scoping(self):
        """exclude(organization=other) is not a scope: it still spans tenants."""
        with self.assertRaises(TenantScopeMissing):
            list(Document.objects.exclude(organization=self.org_b))


class AcrossTenantsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.spec = make_spec()
        cls.org_a = make_org("Alpha Works Ltd", "alpha")
        cls.org_b = make_org("Beta Civils Ltd", "beta")
        make_document(cls.org_a, cls.spec)
        make_document(cls.org_b, cls.spec)

    def test_across_tenants_requires_a_reason(self):
        with self.assertRaises(TenantScopeMissing):
            Document.objects.across_tenants("")

    def test_across_tenants_with_reason_reads_everything(self):
        qs = Document.objects.across_tenants(reason="test: deliberate aggregate")
        self.assertEqual(qs.count(), 2)

    def test_across_tenants_logs_a_warning(self):
        with self.assertLogs("core.tenancy", level="WARNING") as captured:
            Document.objects.across_tenants(reason="test: audit trail").count()
        self.assertIn("cross-tenant query", captured.output[0])
        self.assertIn("test: audit trail", captured.output[0])


class ActiveOrgContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.spec = make_spec()
        cls.org = make_org("Alpha Works Ltd", "alpha")
        make_document(cls.org, cls.spec)

    def test_for_active_org_without_context_raises(self):
        with self.assertRaises(NoActiveOrganization):
            Document.objects.for_active_org().count()

    def test_for_active_org_inside_context(self):
        with active_org(self.org):
            self.assertEqual(Document.objects.for_active_org().count(), 1)

    def test_context_is_restored_on_exit(self):
        with active_org(self.org):
            self.assertEqual(get_active_org_id(), self.org.pk)
        self.assertIsNone(get_active_org_id())

    def test_context_is_restored_after_exception(self):
        try:
            with active_org(self.org):
                raise ValueError("boom")
        except ValueError:
            pass
        self.assertIsNone(get_active_org_id())

    def test_save_without_org_uses_active_context(self):
        with active_org(self.org):
            doc = Document(title="Implicit", file="x.pdf")
            doc.save()
        self.assertEqual(doc.organization_id, self.org.pk)

    def test_save_without_org_or_context_raises(self):
        with self.assertRaises(NoActiveOrganization):
            Document(title="Orphan", file="x.pdf").save()


@override_settings(TENANCY_STRICT=False)
class StrictnessToggleTests(TestCase):
    """
    The guard can be relaxed, and that is recorded here so nobody discovers it
    by accident.  It exists for data migrations and one-off shell forensics --
    never for application code, and never in the test settings.
    """

    @classmethod
    def setUpTestData(cls):
        cls.spec = make_spec()
        cls.org = make_org("Alpha Works Ltd", "alpha")
        make_document(cls.org, cls.spec)

    def test_unscoped_read_is_permitted_when_strictness_is_off(self):
        self.assertEqual(Document.objects.count(), 1)
