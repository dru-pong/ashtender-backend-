"""
Source harvesting.

Every test replaces harvest._transport so no HTTP call is made -- the same
discipline the LLM gateway tests use.  The point of these tests is to prove the
config-driven abstraction holds (a source is a row + JSON, not code), that the
robots flag is honoured, and that first-sighting-wins tracking is correct.
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.enums import Channel, HarvestStrategy
from tenders import harvest
from tenders.models import Source, Tender, TenderSighting


FEED_CONFIG = {
    "items_path": "releases",
    "fields": {
        "external_id": "ocid",
        "reference_number": "tender.id",
        "title": "tender.title",
        "description": "tender.description",
        "url": "tender.url",
        "entity_name": "buyer.name",
        "published_at": "date",
        "closing_at": "tender.tenderPeriod.endDate",
        "object_type": "tender.mainProcurementCategory",
    },
}


def _feed_payload(ref="MRH/2025/1", title="Reshaping of Feeder Road", ocid="ocds-x-1"):
    return {
        "releases": [
            {
                "ocid": ocid,
                "date": "2025-03-01T09:00:00Z",
                "buyer": {"name": "Ghana Highway Authority"},
                "tender": {
                    "id": ref,
                    "title": title,
                    "description": "Spot improvement.",
                    "url": "https://example.gov.gh/notice/1",
                    "mainProcurementCategory": "works",
                    "tenderPeriod": {"endDate": "2025-03-14T17:00:00Z"},
                },
            }
        ]
    }


def _make_source(slug="feed-a", robots=True, config=None):
    return Source.objects.create(
        name=slug, slug=slug, channel=Channel.GOV_GHANEPS,
        strategy=HarvestStrategy.FEED, base_url="https://example.gov.gh/feed.json",
        config=config or FEED_CONFIG, robots_allows_automation=robots,
    )


class FeedHarvestTests(TestCase):
    def test_feed_creates_tender_and_sighting(self):
        source = _make_source()
        with mock.patch.object(harvest, "_transport", return_value=_feed_payload()):
            stats = harvest.harvest_source(source)
        self.assertEqual(stats.tenders_created, 1)
        self.assertEqual(stats.sightings_created, 1)

        tender = Tender.objects.get()
        self.assertEqual(tender.reference_number, "MRH/2025/1")
        self.assertEqual(tender.entity.name, "Ghana Highway Authority")
        self.assertEqual(tender.source_url, "https://example.gov.gh/notice/1")

        sighting = TenderSighting.objects.get()
        self.assertTrue(sighting.is_first_sighting)

    def test_reharvest_does_not_duplicate(self):
        source = _make_source()
        with mock.patch.object(harvest, "_transport", return_value=_feed_payload()):
            harvest.harvest_source(source)
            harvest.harvest_source(source)
        self.assertEqual(Tender.objects.count(), 1)
        self.assertEqual(TenderSighting.objects.count(), 1)

    def test_last_harvested_at_is_set(self):
        source = _make_source()
        with mock.patch.object(harvest, "_transport", return_value=_feed_payload()):
            harvest.harvest_source(source)
        source.refresh_from_db()
        self.assertIsNotNone(source.last_harvested_at)
        self.assertEqual(source.last_error, "")

    def test_fetch_failure_is_recorded_not_raised(self):
        source = _make_source()
        with mock.patch.object(harvest, "_transport", side_effect=RuntimeError("boom")):
            harvest.harvest_source(source)  # must not raise
        source.refresh_from_db()
        self.assertIn("boom", source.last_error)

    def test_a_second_source_is_just_a_different_config(self):
        """
        The abstraction: onboarding a second, differently-shaped feed is a new
        Source row with a different fields map -- no new code.
        """
        alt_config = {
            "items_path": "data.notices",
            "fields": {
                "reference_number": "ref",
                "title": "subject",
                "entity_name": "procuring_entity",
                "url": "link",
            },
        }
        alt_payload = {
            "data": {
                "notices": [
                    {
                        "ref": "DA/2025/09",
                        "subject": "Construction of CHPS Compound",
                        "procuring_entity": "Tano South District Assembly",
                        "link": "https://da.example.gov.gh/n/9",
                    }
                ]
            }
        }
        source = _make_source(slug="da-feed", config=alt_config)
        with mock.patch.object(harvest, "_transport", return_value=alt_payload):
            stats = harvest.harvest_source(source)
        self.assertEqual(stats.tenders_created, 1)
        self.assertEqual(Tender.objects.get().reference_number, "DA/2025/09")


class RobotsFlagTests(TestCase):
    def test_disallowed_source_records_sighting_but_not_source_url(self):
        source = _make_source(slug="no-bots", robots=False)
        with mock.patch.object(harvest, "_transport", return_value=_feed_payload()):
            harvest.harvest_source(source)
        tender = Tender.objects.get()
        # Sighting still recorded (timing is the point), but the source URL is
        # not trusted as an authoritative notice fetch.
        self.assertEqual(tender.source_url, "")
        sighting = TenderSighting.objects.get()
        self.assertTrue(sighting.raw_payload.get("discovery_only"))
        self.assertEqual(
            sighting.raw_payload.get("notice_url"), "https://example.gov.gh/notice/1"
        )


class FirstSightingWinsTests(TestCase):
    def test_earlier_timestamp_wins_even_if_harvested_later(self):
        fast = _make_source(slug="fast")
        slow = _make_source(slug="slow")

        now = timezone.now()
        # The slow channel is harvested FIRST but observed the tender LATER.
        slow_item = harvest.HarvestedItem(
            reference_number="REF-1", title="Upgrading of Roads",
            entity_name="GHA", url="https://slow/1",
        )
        fast_item = harvest.HarvestedItem(
            reference_number="REF-1", title="Upgrading of Roads",
            entity_name="GHA", url="https://fast/1",
        )
        from tenders.harvest import HarvestStats, ingest_item

        stats = HarvestStats()
        ingest_item(slow, slow_item, stats, seen_at=now)
        ingest_item(fast, fast_item, stats, seen_at=now - timedelta(hours=6))

        fast.refresh_from_db()
        slow.refresh_from_db()
        self.assertEqual(fast.first_sighting_wins_count, 1)
        self.assertEqual(slow.first_sighting_wins_count, 0)

        winner = TenderSighting.objects.get(is_first_sighting=True)
        self.assertEqual(winner.source, fast)


class HarvestCommandTests(TestCase):
    def test_command_runs_a_named_source(self):
        _make_source(slug="feed-a")
        out = StringIO()
        with mock.patch.object(harvest, "_transport", return_value=_feed_payload()):
            call_command("harvest_sources", slug="feed-a", stdout=out)
        self.assertEqual(Tender.objects.count(), 1)
        self.assertIn("harvest complete", out.getvalue())
