"""
Discipline mining.

The core invariant under test: a Discipline node exists only because a real
title created it, and ``ocds_title_count`` reflects how many titles landed on
it.  Nothing is pre-invented.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from catalog.models import Discipline, ProcuringEntity
from core.enums import Channel, ObjectType
from tenders.disciplines import match_title, mine_titles, parse_quantity
from tenders.models import Tender, TenderDiscipline


class MatchTitleTests(TestCase):
    """Pure matcher: no database."""

    def test_classroom_block(self):
        matches = match_title("Construction of 1No. 3-Unit Classroom Block")
        self.assertIn("classroom-block", [m.slug for m in matches])

    def test_borehole_drilling(self):
        matches = match_title("Drilling of 10No. Boreholes fitted with Handpumps")
        slugs = [m.slug for m in matches]
        self.assertIn("borehole", slugs)

    def test_feeder_road(self):
        matches = match_title(
            "Reshaping and Spot Improvement of Bechem-Kwasu Feeder Road"
        )
        self.assertIn("feeder-road", [m.slug for m in matches])

    def test_title_can_match_more_than_one_discipline(self):
        matches = match_title("Construction of Classroom Block and 6-Seater KVIP")
        slugs = {m.slug for m in matches}
        self.assertIn("classroom-block", slugs)
        self.assertIn("sanitation-facility", slugs)

    def test_unmatched_title_returns_empty(self):
        # Deliberately not forced onto a nearest node.
        self.assertEqual(match_title("Provision of legal advisory retainer"), [])

    def test_quantity_parse(self):
        self.assertEqual(parse_quantity("Drilling of 10No. Boreholes"), (10.0, "No."))
        self.assertEqual(parse_quantity("Upgrading of 3.2km Town Roads"), (3.2, "km"))
        self.assertEqual(parse_quantity("Supply of stationery"), (None, ""))


class MineTitlesTests(TestCase):
    def setUp(self):
        self.entity = ProcuringEntity.objects.create(
            name="Tano South District Assembly", slug="tano-south-da",
            channel=Channel.GOV_ENTITY,
        )

    def _tender(self, title, ref):
        return Tender.objects.create(
            entity=self.entity, reference_number=ref, title=title,
            object_type=ObjectType.WORKS,
        )

    def test_node_created_only_when_a_title_lands_on_it(self):
        self.assertEqual(Discipline.objects.count(), 0)
        t = self._tender("Construction of 1No. 3-Unit Classroom Block", "R1")
        mine_titles([t])
        self.assertEqual(Discipline.objects.count(), 1)
        node = Discipline.objects.get()
        self.assertEqual(node.slug, "classroom-block")
        self.assertEqual(node.ocds_title_count, 1)

    def test_count_accumulates_over_multiple_titles(self):
        tenders = [
            self._tender("Construction of 3-Unit Classroom Block", "R1"),
            self._tender("Construction of 6-Unit Classroom Block", "R2"),
        ]
        mine_titles(tenders)
        node = Discipline.objects.get(slug="classroom-block")
        self.assertEqual(node.ocds_title_count, 2)

    def test_tenders_are_tagged(self):
        t = self._tender("Drilling of 10No. Boreholes fitted with Handpumps", "R1")
        mine_titles([t])
        tag = TenderDiscipline.objects.get(tender=t)
        self.assertEqual(tag.discipline.slug, "borehole")
        self.assertEqual(tag.quantity, 10)
        self.assertEqual(tag.unit, "No.")

    def test_no_tag_mode_creates_nodes_without_tagging(self):
        t = self._tender("Construction of Classroom Block", "R1")
        mine_titles([t], tag_tenders=False)
        self.assertEqual(Discipline.objects.count(), 1)
        self.assertEqual(TenderDiscipline.objects.count(), 0)

    def test_unmatched_title_creates_nothing(self):
        t = self._tender("Provision of external audit services", "R1")
        stats = mine_titles([t])
        self.assertEqual(Discipline.objects.count(), 0)
        self.assertEqual(stats.titles_unmatched, 1)

    def test_domain_is_linked_when_seeded(self):
        # Seed catalog so the domain codes referenced by the lexicon exist.
        call_command("seed_catalog", quiet_summary=True, stdout=StringIO())
        t = self._tender("Reshaping of Bechem Feeder Road", "R1")
        mine_titles([t])
        node = Discipline.objects.get(slug="feeder-road")
        self.assertIsNotNone(node.domain)
        self.assertEqual(node.domain.code, "road-maintenance")


class SimilarWorksIntegrationTests(TestCase):
    """
    The acceptance criterion for spec 4.5: once a tender is tagged, the
    engine's SIMILAR_WORKS finding is no longer UNKNOWN on every tender.
    """

    def test_mining_flips_similar_works_off_unknown(self):
        from core.tests.factories import complete_five_years, make_org, make_tender
        from core.enums import Verdict
        from eligibility.engine import evaluate
        from eligibility.models import RequirementFinding
        from vault.models import PastProject

        org = make_org()
        tender = make_tender(title="Construction of 1No. 3-Unit Classroom Block")

        # Before mining: untagged tender -> UNKNOWN on similar works.
        before = evaluate(org, tender).findings.get(
            code=RequirementFinding.Code.SIMILAR_WORKS
        )
        self.assertEqual(before.verdict, Verdict.UNKNOWN)

        # Mine, which tags the tender with the classroom-block discipline.
        mine_titles([tender])
        node = Discipline.objects.get(slug="classroom-block")

        # Give the org a matching completed project on the same discipline.
        project = PastProject.objects.create(
            organization=org, name="6-unit block, 2023",
            completed_on=__import__("datetime").date(2023, 6, 1),
        )
        project.disciplines.add(node)

        after = evaluate(org, tender).findings.get(
            code=RequirementFinding.Code.SIMILAR_WORKS
        )
        self.assertEqual(after.verdict, Verdict.PASS)


class MineDisciplinesCommandTests(TestCase):
    def test_command_mines_and_tags(self):
        entity = ProcuringEntity.objects.create(
            name="Ghana Highway Authority", slug="gha", channel=Channel.GOV_ENTITY
        )
        Tender.objects.create(
            entity=entity, reference_number="R1",
            title="Construction of 1No. 3-Unit Classroom Block",
            object_type=ObjectType.WORKS,
        )
        out = StringIO()
        call_command("mine_disciplines", stdout=out)
        self.assertEqual(TenderDiscipline.objects.count(), 1)
        self.assertIn("discipline mining complete", out.getvalue())
