"""
OCDS parse + ingest.

The parse tests use no database (they mirror the pure-parser discipline in
catalog.classification); the ingest tests exercise dedup, entity resolution by
party id, and winner-name normalisation into the competitor graph.
"""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from catalog.models import ProcuringEntity
from core.enums import Method, ObjectType
from tenders.models import AwardHistory, Tender
from tenders.ocds import (
    IngestStats,
    ingest_releases,
    iter_releases,
    parse_release,
)


def _release(**overrides):
    base = {
        "ocid": "ocds-uhveoc-0001",
        "date": "2025-03-01T09:00:00Z",
        "buyer": {"id": "GH-MDA-1001", "name": "Ghana Highway Authority"},
        "tender": {
            "id": "MRH/RF/2025/014",
            "title": "Reshaping of Bechem-Kwasu Feeder Road",
            "description": "Spot improvement and reshaping.",
            "mainProcurementCategory": "works",
            "procurementMethodDetails": "National Competitive Tendering",
            "status": "active",
            "value": {"amount": "1200000.00", "currency": "GHS"},
            "tenderPeriod": {"endDate": "2025-03-14T17:00:00Z"},
        },
    }
    base.update(overrides)
    return base


class ParseReleaseTests(TestCase):
    def test_flattens_core_fields(self):
        parsed = parse_release(_release())
        self.assertEqual(parsed.ocid, "ocds-uhveoc-0001")
        self.assertEqual(parsed.buyer_name, "Ghana Highway Authority")
        self.assertEqual(parsed.buyer_ocds_party_id, "GH-MDA-1001")
        self.assertEqual(parsed.reference_number, "MRH/RF/2025/014")
        self.assertEqual(parsed.object_type, ObjectType.WORKS)
        self.assertEqual(parsed.method, Method.NCT)
        self.assertEqual(parsed.estimated_value, Decimal("1200000.00"))
        self.assertEqual(parsed.currency, "GHS")
        self.assertIsNotNone(parsed.closing_at)

    def test_is_usable_requires_ocid_and_buyer(self):
        self.assertTrue(parse_release(_release()).is_usable)
        self.assertFalse(parse_release(_release(ocid="")).is_usable)
        self.assertFalse(parse_release(_release(buyer={})).is_usable)

    def test_buyer_name_resolved_from_parties_when_block_has_id_only(self):
        rel = _release(
            buyer={"id": "GH-MDA-1001"},
            parties=[{"id": "GH-MDA-1001", "name": "Ghana Highway Authority"}],
        )
        self.assertEqual(parse_release(rel).buyer_name, "Ghana Highway Authority")

    def test_cancelled_status_is_flagged(self):
        rel = _release()
        rel["tender"]["status"] = "cancelled"
        self.assertTrue(parse_release(rel).is_cancelled)

    def test_awards_are_normalised(self):
        rel = _release(
            awards=[
                {
                    "status": "active",
                    "date": "2025-04-02",
                    "value": {"amount": "1180000", "currency": "GHS"},
                    "suppliers": [{"name": "Shamma Rohi Company Ltd."}],
                }
            ]
        )
        parsed = parse_release(rel)
        self.assertEqual(len(parsed.awards), 1)
        award = parsed.awards[0]
        self.assertEqual(award.winner_name, "Shamma Rohi Company Ltd.")
        # Same normalisation register matching uses: suffix + punctuation gone.
        self.assertEqual(award.winner_name_normalised, "SHAMMA ROHI")
        self.assertEqual(award.award_date, date(2025, 4, 2))

    def test_cancelled_award_is_dropped(self):
        rel = _release(
            awards=[{"status": "cancelled", "suppliers": [{"name": "X Ltd"}]}]
        )
        self.assertEqual(parse_release(rel).awards, [])

    def test_garbage_input_does_not_raise(self):
        self.assertFalse(parse_release("not a dict").is_usable)
        self.assertFalse(parse_release({}).is_usable)


class IterReleasesTests(TestCase):
    def test_reads_release_package(self):
        package = '{"releases": [%s, %s]}' % (
            _json(_release()),
            _json(_release(ocid="ocds-uhveoc-0002")),
        )
        self.assertEqual(len(list(iter_releases(package))), 2)

    def test_reads_record_package_compiled_release(self):
        package = '{"records": [{"compiledRelease": %s}]}' % _json(_release())
        got = list(iter_releases(package))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["ocid"], "ocds-uhveoc-0001")

    def test_reads_ndjson_and_skips_bad_lines(self):
        ndjson = "\n".join([_json(_release()), "{ broken", _json(_release(ocid="x"))])
        self.assertEqual(len(list(iter_releases(ndjson))), 2)


class IngestReleasesTests(TestCase):
    def test_creates_tender_entity_and_award(self):
        rel = _release(
            awards=[
                {
                    "date": "2025-04-02",
                    "value": {"amount": "1180000", "currency": "GHS"},
                    "suppliers": [{"name": "Shamma Rohi Limited"}],
                }
            ]
        )
        stats = ingest_releases([rel])
        self.assertEqual(stats.tenders_created, 1)
        self.assertEqual(stats.entities_created, 1)
        self.assertEqual(stats.awards_created, 1)

        tender = Tender.objects.get()
        self.assertEqual(tender.reference_number, "MRH/RF/2025/014")
        self.assertEqual(tender.entity.ocds_party_id, "GH-MDA-1001")
        # raw_text is filled so requirement extraction has something to read.
        self.assertIn("Reshaping", tender.raw_text)

        award = AwardHistory.objects.get()
        self.assertEqual(award.winner_name_normalised, "SHAMMA ROHI")

    def test_reingesting_same_corpus_updates_not_duplicates(self):
        rel = _release()
        ingest_releases([rel])
        stats = ingest_releases([rel])
        self.assertEqual(Tender.objects.count(), 1)
        self.assertEqual(stats.tenders_created, 0)
        self.assertEqual(stats.tenders_updated, 1)

    def test_same_buyer_party_id_is_one_entity(self):
        second = _release(ocid="ocds-uhveoc-0002")
        second["tender"]["id"] = "MRH/RF/2025/015"
        ingest_releases([_release(), second])
        # Two tenders, same buyer -> one entity node in the competitor graph.
        self.assertEqual(Tender.objects.count(), 2)
        self.assertEqual(ProcuringEntity.objects.count(), 1)

    def test_renamed_buyer_same_party_id_does_not_split(self):
        ingest_releases([_release()])
        renamed = _release(
            ocid="ocds-uhveoc-0003",
            buyer={"id": "GH-MDA-1001", "name": "Ghana Highways Authority (renamed)"},
        )
        ingest_releases([renamed])
        self.assertEqual(ProcuringEntity.objects.count(), 1)

    def test_award_only_release_still_records_the_win(self):
        rel = {
            "ocid": "ocds-uhveoc-0009",
            "buyer": {"id": "GH-MDA-2002", "name": "Ministry of Health"},
            "awards": [
                {"date": "2025-05-01", "suppliers": [{"name": "Medico Ghana Ltd"}]}
            ],
        }
        stats = ingest_releases([rel])
        self.assertEqual(stats.awards_created, 1)
        self.assertEqual(AwardHistory.objects.get().winner_name, "Medico Ghana Ltd")

    def test_unusable_release_is_skipped(self):
        stats = ingest_releases([_release(ocid="")])
        self.assertEqual(stats.releases_skipped, 1)
        self.assertEqual(Tender.objects.count(), 0)


class IngestCommandTests(TestCase):
    def test_command_reads_a_file(self):
        import tempfile

        package = '{"releases": [%s]}' % _json(_release())
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write(package)
            temp_path = handle.name

        out = StringIO()
        call_command("ingest_ocds", path=temp_path, stdout=out)
        self.assertEqual(Tender.objects.count(), 1)
        self.assertIn("OCDS ingest complete", out.getvalue())


def _json(obj):
    import json

    return json.dumps(obj)
