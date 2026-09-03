"""
Load MMDAs from a CSV so the district catalogue comes from a published source
rather than from anybody's memory.

Expected header: region_code,name,kind,capital
Kind is METROPOLITAN | MUNICIPAL | DISTRICT (case-insensitive).

Pass --mark-complete REGION_CODE once a region's full list is loaded; that flips
Region.district_seed_complete, which is what anything district-filtered checks
before presenting itself as comprehensive.
"""
import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import District, Region


class Command(BaseCommand):
    help = "Load districts (MMDAs) from CSV: region_code,name,kind,capital"

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--mark-complete", nargs="*", default=[], metavar="REGION_CODE")

    @transaction.atomic
    def handle(self, *args, **options):
        regions = {region.code: region for region in Region.objects.all()}
        if not regions:
            raise CommandError("No regions found. Run 'manage.py seed_catalog' first.")

        created = updated = skipped = 0
        with open(options["csv_path"], newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = {"region_code", "name"} - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"CSV is missing required column(s): {sorted(missing)}")
            for row in reader:
                region_code = (row.get("region_code") or "").strip().upper()
                name = (row.get("name") or "").strip()
                if not region_code or not name:
                    skipped += 1
                    continue
                region = regions.get(region_code)
                if region is None:
                    self.stderr.write(f"unknown region_code {region_code!r} for {name!r}")
                    skipped += 1
                    continue
                kind = (row.get("kind") or "DISTRICT").strip().upper()
                if kind not in District.Kind.values:
                    kind = District.Kind.DISTRICT
                _, was_created = District.objects.update_or_create(
                    region=region, name=name,
                    defaults={"kind": kind, "capital": (row.get("capital") or "").strip()},
                )
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

        for region_code in options["mark_complete"]:
            count = Region.objects.filter(code=region_code.upper()).update(
                district_seed_complete=True
            )
            if not count:
                self.stderr.write(f"--mark-complete: unknown region {region_code!r}")

        total = District.objects.count()
        self.stdout.write(
            f"created={created} updated={updated} skipped={skipped} total_districts={total}"
        )
        remaining = 261 - total
        if remaining > 0:
            self.stdout.write(
                self.style.WARNING(f"{remaining} of the 261 MMDAs are still not loaded.")
            )
