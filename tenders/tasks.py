"""
Scheduled data-acquisition work: OCDS ingest and source harvesting.

Every task here is a thin wrapper over the plain functions in ``ocds.py`` and
``harvest.py``.  The functions are where the logic lives and where the tests
point; the tasks exist only to put that logic on the beat schedule and the
worker.  Nothing here reaches a model provider directly -- requirement
extraction is queued separately through the llm gateway.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def ingest_ocds_file(path: str) -> dict:
    """Ingest an OCDS corpus file that was already downloaded to ``path``."""
    from django.db import transaction

    from tenders.ocds import ingest_releases, iter_releases

    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    with transaction.atomic():
        stats = ingest_releases(iter_releases(raw))
    logger.info("OCDS ingest of %s: %s", path, stats.as_dict())
    return stats.as_dict()


@shared_task
def mine_disciplines_task() -> dict:
    """Mine disciplines from all tender titles and tag tenders."""
    from django.db import transaction

    from tenders.disciplines import mine_titles
    from tenders.models import Tender

    with transaction.atomic():
        stats = mine_titles(Tender.objects.all().only("id", "title"))
    logger.info("discipline mining: %s", stats.as_dict())
    return stats.as_dict()


@shared_task
def harvest_all_sources() -> dict:
    """
    Harvest every active source.

    One source's failure is captured on its row (see ``harvest_source``) and
    does not abort the sweep, so a single broken feed cannot starve the others.
    """
    from tenders.harvest import HarvestStats, harvest_source
    from tenders.models import Source

    stats = HarvestStats()
    count = 0
    for source in Source.objects.filter(is_active=True):
        harvest_source(source, stats=stats)
        count += 1
    result = stats.as_dict()
    result["sources_harvested"] = count
    logger.info("harvest sweep: %s", result)
    return result


@shared_task
def harvest_one_source(source_id: int) -> dict:
    """Harvest a single source on demand."""
    from tenders.harvest import harvest_source
    from tenders.models import Source

    source = Source.objects.get(pk=source_id)
    stats = harvest_source(source)
    return stats.as_dict()
