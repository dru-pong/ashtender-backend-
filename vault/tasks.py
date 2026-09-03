"""
Post-upload document processing.

The sequence the handoff (4.4) specifies: accept an upload -> set
``content_hash`` via ``compute_hash()`` -> render pages to
``DocumentPage.image`` -> queue ``llm.tasks.extract_document_fields``.

Kept as one task so the ordering is guaranteed and a single retry re-runs the
whole chain.  The task is tenant-scoped through ``active_org`` because it
touches TenantOwned rows (Document, DocumentPage) and the tenancy guard refuses
to evaluate otherwise.

The invariant from the brief holds by construction: this task sets
``content_hash``, ``page_count`` and (via the extraction task) ``extracted``,
but never touches ``verified_by_human``.  Only a person pressing confirm in
``DocumentViewSet.confirm`` moves that flag.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def process_uploaded_document(document_id: int, organization_id: int) -> dict:
    """Hash, render, and queue extraction for a freshly uploaded document."""
    from core.tenancy import active_org
    from tenancy.models import Organization
    from vault.models import Document
    from vault.rendering import render_document_pages

    organization = Organization.objects.get(pk=organization_id)
    with active_org(organization):
        document = Document.objects.for_org(organization_id).get(pk=document_id)

        # 1. Content hash. Cheap, and it is what makes re-extraction of an
        #    unchanged document a cache read in the LLM gateway.
        if document.file and not document.content_hash:
            try:
                document.content_hash = document.compute_hash()
                document.save(update_fields=["content_hash"])
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("hash failed for document %s: %s", document_id, exc)

        # 2. Render pages to images a human can later confirm crops against.
        pages = render_document_pages(document)
        if not pages:
            return {"document_id": document_id, "status": "no_pages_rendered", "pages": 0}

    # 3. Queue extraction. Outside the active_org block: the extraction task
    #    re-enters its own tenant scope, and chaining by id keeps the two tasks
    #    independently retryable.
    from llm.tasks import extract_document_fields

    extract_document_fields.delay(document_id, organization_id)
    return {"document_id": document_id, "status": "queued_extraction", "pages": pages}
