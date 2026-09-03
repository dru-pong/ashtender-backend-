"""
Mine the discipline taxonomy from tender titles already in the database.

Run this after ``ingest_ocds`` has populated real titles.  It creates a
Discipline node only when a real title lands on it, bumps ``ocds_title_count``
as evidence of coverage, and tags each tender so the eligibility engine's
SIMILAR_WORKS check stops returning UNKNOWN on every tender.

    python manage.py ingest_ocds --path corpus.json
    python manage.py mine_disciplines

``--no-tag`` mines the tree (creating nodes and counts) without writing
TenderDiscipline rows, for a dry look at what the corpus would produce.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from tenders.disciplines import mine_titles
from tenders.models import Tender


class Command(BaseCommand):
    help = "Mine disciplines from tender titles and tag tenders."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-tag", action="store_true",
            help="Create nodes and counts but do not write TenderDiscipline rows.",
        )
        parser.add_argument(
            "--quiet-summary", action="store_true", help="Suppress the closing summary."
        )

    def handle(self, *args, **options):
        tenders = Tender.objects.all().only("id", "title")
        with transaction.atomic():
            stats = mine_titles(tenders, tag_tenders=not options["no_tag"])

        if not options.get("quiet_summary"):
            for key, value in stats.as_dict().items():
                self.stdout.write(f"  {key.ljust(20)}  {value}")
            self.stdout.write(self.style.SUCCESS("discipline mining complete"))
