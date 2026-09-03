"""LLM-backed background work.  Every call still goes through the gateway."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def extract_document_fields(document_id: int, organization_id: int) -> dict:
    """
    Read a document's dates and reference numbers with a vision model.

    Writes ``extracted=True`` and leaves ``verified_by_human`` alone. That
    split is the point: an extracted date is enough to screen a tender in and
    never enough to submit a bid on, and only a person pressing confirm moves
    it across that line.
    """
    from core.enums import LLMTask
    from core.tenancy import active_org
    from llm.gateway import complete
    from tenancy.models import Organization
    from vault.models import Document, ExtractedField

    organization = Organization.objects.get(pk=organization_id)
    with active_org(organization):
        document = Document.objects.for_org(organization_id).get(pk=document_id)
        pages = list(document.pages.order_by("page_number")[:4])
        images = [page.image.url for page in pages if page.image]
        if not images:
            return {"document_id": document_id, "status": "no_pages_rendered"}

        prompt = (
            "Extract the following fields from this Ghanaian compliance "
            "document, if present: issue date, expiry date, reference or "
            "certificate number, issuing authority, and the company name it "
            "was issued to. Return a normalised bounding box for each value."
        )
        result = complete(
            LLMTask.DOC_EXTRACT, prompt, organization=organization, images=images
        )
        if not result.ok:
            logger.warning(
                "extraction failed for document %s: %s", document_id, result.error
            )
            return {"document_id": document_id, "status": result.status}

        page_by_number = {page.page_number: page for page in pages}
        written = 0
        for field in (result.data or {}).get("fields", []):
            extracted = ExtractedField(
                organization=organization,
                document=document,
                page=page_by_number.get(field.get("page", 1)),
                field_name=str(field.get("field", ""))[:64],
                value_raw=str(field.get("value", "")),
                bbox=field.get("bbox"),
                confidence=field.get("confidence"),
                llm_call=result.call,
            )
            if not extracted.bbox_is_usable:
                # A value with no usable box cannot be confirmed against a
                # crop, so it is dropped rather than stored unverifiable.
                extracted.bbox = None
            extracted.save()
            written += 1

        document.extracted = True
        document.save(update_fields=["extracted"])

    return {"document_id": document_id, "status": result.status, "fields_written": written}


@shared_task
def extract_tender_requirements(tender_id: int) -> dict:
    """
    Pull the document requirements out of a notice, each with its sentence.

    Catalogue-wide work, so no organization is attached to the LLMCall: a fake
    one would corrupt exactly the per-tenant cost attribution the ledger exists
    for.
    """
    from catalog.models import RequirementSpec
    from core.enums import LLMTask, RequirementOrigin, RequirementTier
    from llm.gateway import complete
    from tenders.models import Tender, TenderRequirement

    tender = Tender.objects.get(pk=tender_id)
    if not tender.raw_text:
        return {"tender_id": tender_id, "status": "no_raw_text"}

    known = list(RequirementSpec.objects.values_list("code", "name", "doc_type"))
    catalogue = "\n".join(f"- {code}: {name}" for code, name, _ in known)
    prompt = (
        "Below is the text of a Ghanaian public tender notice. List every "
        "document or credential a tenderer must supply. For each one, quote "
        "the exact sentence it appears in as source_text; a requirement "
        "without its sentence is not usable. Match to one of these codes where "
        f"possible:\n{catalogue}\n\nNOTICE TEXT:\n{tender.raw_text[:12000]}"
    )
    result = complete(LLMTask.REQ_EXTRACT, prompt)
    if not result.ok:
        return {"tender_id": tender_id, "status": result.status}

    specs_by_code = {spec.code: spec for spec in RequirementSpec.objects.all()}
    written = 0
    for item in (result.data or {}).get("requirements", []):
        spec = specs_by_code.get(str(item.get("doc_type", "")).strip().lower())
        if spec is None:
            continue
        TenderRequirement.objects.update_or_create(
            tender=tender, spec=spec,
            defaults={
                "origin": RequirementOrigin.EXPLICIT,
                "tier": RequirementTier.TENDER_EXPLICIT,
                "is_mandatory": bool(item.get("is_mandatory", True)),
                "source_text": str(item.get("source_text", ""))[:4000],
                "extracted_by": result.call,
                "confidence": item.get("confidence"),
            },
        )
        written += 1

    classification_text = (result.data or {}).get("classification_text", "")
    if classification_text:
        _store_class_requirements(tender, classification_text)

    return {"tender_id": tender_id, "status": result.status, "requirements": written}


def _store_class_requirements(tender, text: str) -> int:
    """
    Parse and store classification requirements from extracted text.

    The parse is pure Python (catalog.classification), not a model output: the
    model supplies the sentence, the regex supplies the meaning.
    """
    from catalog.classification import parse_classification_notation, validate_against_catalog
    from core.enums import RequirementOrigin
    from tenders.models import TenderClassRequirement

    parsed = parse_classification_notation(text)
    recognised, rejected = validate_against_catalog(parsed.requirements)
    for requirement, category in recognised:
        TenderClassRequirement.objects.update_or_create(
            tender=tender, scheme=category.scheme, category=category,
            defaults={
                "min_financial_class": requirement.min_financial_class,
                "or_better": requirement.or_better,
                "source_text": requirement.source_text[:4000],
                "origin": RequirementOrigin.EXPLICIT,
            },
        )
    for requirement, reason in rejected:
        logger.warning("tender %s: rejected class %s (%s)", tender.pk, requirement, reason)
    return len(recognised)
