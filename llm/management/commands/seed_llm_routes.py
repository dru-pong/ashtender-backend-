"""
Seed one ModelRoute per task.

Model names are OpenAI-compatible identifiers passed straight through to the
configured base URL, so retargeting the whole system at a different provider is
an environment change plus an admin edit, not a code change.

Costs default to zero.  Set the real per-1k figures for whatever model each
route actually points at, or the LLMCall ledger will report every extraction as
free -- which is exactly the number that must not be wrong before pricing is set.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.enums import LLMTask
from llm.models import ModelRoute
from llm.schema import (
    GROUNDED_EXTRACTION_SCHEMA,
    REQUIREMENT_EXTRACTION_SCHEMA,
    TITLE_PARSE_SCHEMA,
)

EXTRACT_ONLY = (
    "You extract information. You never decide whether a company is eligible, "
    "qualified, or compliant. If asked for a judgement, return the underlying "
    "facts instead. Reply with JSON only: no prose, no code fences."
)

ROUTES = [
    (LLMTask.DOC_CLASSIFY, "qwen2.5:7b", 0.0, 512, {
        "type": "object", "required": ["doc_type", "confidence"],
        "properties": {
            "doc_type": {"type": "string"},
            "issuing_authority": {"type": "string"},
            "confidence": {"type": "number"},
        },
    }, False,
     EXTRACT_ONLY + " Classify the document into one of the known doc_type codes."),

    (LLMTask.DOC_EXTRACT, "qwen2.5-vl:7b", 0.0, 2048, GROUNDED_EXTRACTION_SCHEMA, True,
     EXTRACT_ONLY + " For every field, return a normalised bounding box "
     "[x0, y0, x1, y1] with values between 0 and 1 locating the value on the "
     "page. A field without a usable box cannot be confirmed by a human, so "
     "omit it rather than guessing the box."),

    (LLMTask.REQ_EXTRACT, "qwen2.5:14b", 0.0, 4096, REQUIREMENT_EXTRACTION_SCHEMA, False,
     EXTRACT_ONLY + " Extract each document or credential the notice requires. "
     "For every one, quote the exact sentence it appears in as source_text. A "
     "requirement without its sentence is not usable."),

    (LLMTask.TITLE_PARSE, "qwen2.5:7b", 0.0, 1024, TITLE_PARSE_SCHEMA, False,
     EXTRACT_ONLY + " Ghanaian works titles are semi-templated, e.g. "
     "'Construction of 1No. 3-Unit Classroom Block', 'Drilling of 10No. "
     "Boreholes fitted with Handpumps', 'Reshaping and Spot Improvement of "
     "X-Y Feeder Road'. Extract quantity and unit (No., Unit, km, m2, m3)."),

    (LLMTask.DISCIPLINE_TAG, "qwen2.5:7b", 0.0, 1024, {
        "type": "object", "required": ["disciplines"],
        "properties": {
            "disciplines": {
                "type": "array",
                "items": {
                    "type": "object", "required": ["slug"],
                    "properties": {
                        "slug": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            }
        },
    }, False, EXTRACT_ONLY + " Choose only from the discipline slugs provided."),

    (LLMTask.ENTITY_ENRICH, "qwen2.5:14b", 0.0, 2048, {
        "type": "object", "required": ["contacts"],
        "properties": {
            "contacts": {
                "type": "array",
                "items": {
                    "type": "object", "required": [],
                    "properties": {
                        "full_name": {"type": "string"},
                        "title": {"type": "string"},
                        "phones": {"type": "array", "items": {"type": "string"}},
                        "email": {"type": "string"},
                        "postal_address": {"type": "string"},
                        "source_text": {"type": "string"},
                    },
                },
            }
        },
    }, False,
     EXTRACT_ONLY + " Extract officer contact details that already appear in "
     "the notice text. Do not infer, complete, or guess a name, number or "
     "address that is not written there."),

    (LLMTask.NARRATIVE, "qwen2.5:14b", 0.3, 4096, {}, False,
     "You draft factual narrative text for tender submissions. Use only the "
     "facts supplied. Never invent a project, a date, a value, or a reference."),
]


class Command(BaseCommand):
    help = "Seed LLM routing table. Idempotent."

    @transaction.atomic
    def handle(self, *args, **options):
        for task, model_name, temperature, max_tokens, schema, vision, prompt in ROUTES:
            # Key on task alone so re-seeding updates the one row per task
            # rather than creating a second row when the model_name has been
            # changed (e.g. by set_model). model_name is set only on creation;
            # an operator's chosen model survives a re-seed, while the schema,
            # prompt, and tuning the seed owns are refreshed every time.
            route, created = ModelRoute.objects.get_or_create(
                task=task,
                defaults={
                    "model_name": model_name,
                    "temperature": temperature, "max_tokens": max_tokens,
                    "json_schema": schema, "supports_vision": vision,
                    "system_prompt": prompt, "is_active": True, "priority": 10,
                },
            )
            if not created:
                route.temperature = temperature
                route.max_tokens = max_tokens
                route.json_schema = schema
                route.system_prompt = prompt
                route.is_active = True
                route.priority = 10
                # model_name and supports_vision are deliberately NOT reset:
                # set_model owns those, and a re-seed must not drag the model
                # back to the seed default.
                route.save(update_fields=[
                    "temperature", "max_tokens", "json_schema",
                    "system_prompt", "is_active", "priority",
                ])
        total = ModelRoute.objects.count()
        tasks = ModelRoute.objects.filter(is_active=True).values_list("task", flat=True)
        uncovered = sorted(set(LLMTask.values) - set(tasks))
        self.stdout.write(f"routes={total} tasks_covered={len(set(tasks))}")
        if uncovered:
            self.stdout.write(self.style.WARNING(f"tasks with no active route: {uncovered}"))
        self.stdout.write(
            self.style.WARNING(
                "cost_per_1k_input/output are 0 on every route. Set them before "
                "reading anything into the LLMCall cost ledger."
            )
        )
        self.stdout.write(self.style.SUCCESS("llm routes seeded"))
