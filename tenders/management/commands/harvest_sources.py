"""
Harvest tender sources into TenderSighting rows.

    python manage.py harvest_sources            # all active sources
    python manage.py harvest_sources --slug ghaneps-feed

Every source is a database row plus JSON selector config; there is no
per-source code.  A fetch failure is recorded on the source's ``last_error``
and does not stop the sweep.
"""
from django.core.management.base import BaseCommand, CommandError

from tenders.harvest import HarvestStats, harvest_source
from tenders.models import Source


class Command(BaseCommand):
    help = "Harvest active tender sources into sightings."

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="Harvest only this source (by slug).")
        parser.add_argument(
            "--quiet-summary", action="store_true", help="Suppress the closing summary."
        )

    def handle(self, *args, **options):
        sources = Source.objects.filter(is_active=True)
        slug = options.get("slug")
        if slug:
            sources = sources.filter(slug=slug)
            if not sources.exists():
                raise CommandError(f"no active source with slug {slug!r}")

        stats = HarvestStats()
        count = 0
        for source in sources:
            harvest_source(source, stats=stats)
            count += 1
            if source.last_error:
                self.stderr.write(f"  {source.slug}: {source.last_error}")

        if not options.get("quiet_summary"):
            self.stdout.write(f"  sources_harvested    {count}")
            for key, value in stats.as_dict().items():
                self.stdout.write(f"  {key.ljust(20)}  {value}")
            self.stdout.write(self.style.SUCCESS("harvest complete"))
