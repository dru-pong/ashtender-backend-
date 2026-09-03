"""
Pin the output JSON shape into each route's system prompt.

The smoke probe against Gemma 4 revealed the real cause of "schema failed": the
prompts described the *task* but never showed the model the required *output
shape*, so it guessed -- differently on every call (a bare object one time, a
bare array the next, a singular key a third).  Validating after the fact and
repairing the guess is whack-a-mole.

The fix is upstream: tell the model the exact JSON envelope, with a worked
example, in the system prompt.  Gemma 4 has native system-prompt support and
strong instruction-following, so a concrete example is enough to make it return
the right shape on the first try.  The gateway's normaliser stays as a safety
net for residual wobble, but this makes the net rarely needed.

Because the system prompt is a ModelRoute field, this is configuration, not
code -- run it after set_model, or after seed_llm_routes, to (re)install the
shape guidance:

    python manage.py set_model gemma4:31b-cloud
    python manage.py pin_prompt_shapes
"""
from django.core.management.base import BaseCommand

from core.enums import LLMTask
from llm.models import ModelRoute

_JSON_RULES = (
    "Reply with ONE JSON object only. No prose, no markdown, no code fences. "
    "Always use the exact top-level key shown below, and always make list "
    "fields a JSON array even when there is a single item or none. "
)

#: task -> (shape guidance appended to the system prompt)
SHAPES = {
    LLMTask.TITLE_PARSE: _JSON_RULES + (
        'Shape: {"works": [{"description": "3-Unit Classroom Block", '
        '"quantity": 1, "unit": "No."}]}. One array element per distinct work '
        "in the title. Ghanaian works titles are semi-templated, e.g. "
        "'Construction of 1No. 3-Unit Classroom Block', 'Drilling of 10No. "
        "Boreholes'. Units are No., Unit, km, m2, m3."
    ),
    LLMTask.DISCIPLINE_TAG: _JSON_RULES + (
        'Shape: {"disciplines": [{"slug": "borehole", "confidence": 0.9}]}. '
        "Choose slug only from the discipline slugs provided in the user "
        "message. confidence is 0-1. Return an empty array if none apply."
    ),
    LLMTask.ENTITY_ENRICH: _JSON_RULES + (
        'Shape: {"contacts": [{"full_name": "Ama Mensah", "title": "District '
        'Coordinating Director", "phones": ["000"], "email": "", '
        '"postal_address": "P.O. Box 1, Sunyani", "source_text": "..."}]}. '
        "Extract only officer contact details that already appear in the notice "
        "text. Do not infer a name, number or address that is not written "
        "there. If a role and phone are given but no personal name is printed, "
        'set full_name to "" -- a titled contact without a name is still valid. '
        'If no officer is named at all, return {"contacts": []}.'
    ),
    LLMTask.DOC_EXTRACT: _JSON_RULES + (
        'Shape: {"fields": [{"field": "expiry_date", "value": "2026-11-30", '
        '"page": 1, "bbox": [0.1, 0.2, 0.4, 0.25], "confidence": 0.9}]}. '
        "bbox is [x0, y0, x1, y1] normalised 0-1 locating the value on the "
        "page. A field with no usable box cannot be confirmed by a human, so "
        "omit it rather than guessing the box. Return {\"fields\": []} if the "
        "page shows none of the requested fields."
    ),
    LLMTask.REQ_EXTRACT: _JSON_RULES + (
        'Shape: {"requirements": [{"doc_type": "tax-clearance-certificate", '
        '"source_text": "Tenderers shall submit a valid TCC.", '
        '"is_mandatory": true, "confidence": 0.9}], "classification_text": ""}. '
        "For every requirement quote the exact sentence as source_text; a "
        "requirement without its sentence is not usable. Match doc_type to one "
        "of the codes in the user message where possible."
    ),
    LLMTask.DOC_CLASSIFY: _JSON_RULES + (
        'Shape: {"doc_type": "vat-registration", "issuing_authority": "GRA", '
        '"confidence": 0.9}. Classify into one of the known doc_type codes.'
    ),
}


class Command(BaseCommand):
    help = "Install shape-pinned system prompts on active LLM routes."

    def handle(self, *args, **options):
        updated = 0
        for task, guidance in SHAPES.items():
            route = (
                ModelRoute.objects.filter(task=task, is_active=True)
                .order_by("-priority")
                .first()
            )
            if route is None:
                self.stdout.write(f"  {task}: no active route, skipped")
                continue
            # Preserve the extract-only preamble (first sentence) if present,
            # then append the shape guidance. Idempotent: replace any prior
            # guidance rather than stacking it.
            base = route.system_prompt.split("Shape:")[0].split("Reply with ONE JSON")[0]
            base = base.strip()
            route.system_prompt = (base + " " + guidance).strip()
            route.save(update_fields=["system_prompt"])
            self.stdout.write(f"  {task.ljust(16)} shape pinned")
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"{updated} route prompt(s) updated"))
