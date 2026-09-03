"""
seed_llm_routes idempotency and model-preservation.

The duplication bug: seeding keyed on (task, model_name), so re-seeding after
set_model changed the model created a second row per task. The seed now keys on
task alone and never resets model_name on re-seed, so these two properties hold.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.enums import LLMTask
from llm.models import ModelRoute


class SeedIdempotencyTests(TestCase):
    def test_reseeding_does_not_duplicate(self):
        call_command("seed_llm_routes", stdout=StringIO())
        first = ModelRoute.objects.count()
        call_command("seed_llm_routes", stdout=StringIO())
        self.assertEqual(ModelRoute.objects.count(), first)
        # Exactly one active route per task.
        for task in {r.task for r in ModelRoute.objects.all()}:
            self.assertEqual(
                ModelRoute.objects.filter(task=task, is_active=True).count(), 1
            )

    def test_reseeding_after_set_model_keeps_the_chosen_model(self):
        call_command("seed_llm_routes", stdout=StringIO())
        call_command("set_model", "gemma4:31b-cloud", stdout=StringIO())
        # Re-seed must not drag the model back to the qwen defaults.
        call_command("seed_llm_routes", stdout=StringIO())
        models = set(ModelRoute.objects.values_list("model_name", flat=True))
        self.assertEqual(models, {"gemma4:31b-cloud"})
        # And still no duplicates.
        self.assertEqual(
            ModelRoute.objects.filter(task=LLMTask.TITLE_PARSE).count(), 1
        )

    def test_reseeding_refreshes_schema_the_seed_owns(self):
        call_command("seed_llm_routes", stdout=StringIO())
        route = ModelRoute.objects.get(task=LLMTask.TITLE_PARSE)
        route.json_schema = {"type": "object", "required": ["bogus"]}
        route.save(update_fields=["json_schema"])
        # Re-seed restores the real schema.
        call_command("seed_llm_routes", stdout=StringIO())
        route.refresh_from_db()
        self.assertIn("works", route.json_schema.get("properties", {}))
