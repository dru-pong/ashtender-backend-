"""
Ingest an OCDS corpus into Tender / AwardHistory / ProcuringEntity.

Reads an OCDS release package, record package, JSON array, or newline-delimited
release stream from a file (``--path``) or stdin.  Nothing is fetched here: the
corpus arrives as a file the operator downloaded, keeping this command free of
any network dependency or robots concern.  A Celery task
(``tenders.tasks.ingest_ocds_file``) wraps the same code for scheduled runs.

    python manage.py ingest_ocds --path corpus.json
    cat corpus.ndjson | python manage.py ingest_ocds
"""
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tenders.ocds import ingest_releases, iter_releases


class Command(BaseCommand):
    help = "Ingest an OCDS release corpus into tenders, awards and entities."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path", help="Path to an OCDS package or NDJSON file. Omit to read stdin."
        )
        parser.add_argument(
            "--quiet-summary", action="store_true", help="Suppress the closing summary."
        )

    def handle(self, *args, **options):
        path = options.get("path")
        if path:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    raw = handle.read()
            except OSError as exc:
                raise CommandError(f"could not read {path}: {exc}")
        else:
            if sys.stdin.isatty():
                raise CommandError("no --path given and nothing on stdin")
            raw = sys.stdin.read()

        with transaction.atomic():
            stats = ingest_releases(iter_releases(raw))

        if not options.get("quiet_summary"):
            for key, value in stats.as_dict().items():
                self.stdout.write(f"  {key.ljust(20)}  {value}")
            self.stdout.write(self.style.SUCCESS("OCDS ingest complete"))
