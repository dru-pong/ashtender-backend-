"""pin_prompt_shapes installs shape guidance onto routes, idempotently."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.enums import LLMTask
from llm.models import ModelRoute


class PinPromptShapesTests(TestCase):
    def setUp(self):
        call_command("seed_llm_routes", stdout=StringIO())

    def test_installs_shape_guidance(self):
        call_command("pin_prompt_shapes", stdout=StringIO())
        route = ModelRoute.objects.get(task=LLMTask.TITLE_PARSE, is_active=True)
        self.assertIn('"works"', route.system_prompt)
        self.assertIn("Shape:", route.system_prompt)

    def test_is_idempotent_does_not_stack(self):
        call_command("pin_prompt_shapes", stdout=StringIO())
        call_command("pin_prompt_shapes", stdout=StringIO())
        route = ModelRoute.objects.get(task=LLMTask.TITLE_PARSE, is_active=True)
        # Only one "Shape:" block, not two.
        self.assertEqual(route.system_prompt.count("Shape:"), 1)

    def test_preserves_extract_only_preamble(self):
        call_command("pin_prompt_shapes", stdout=StringIO())
        route = ModelRoute.objects.get(task=LLMTask.DISCIPLINE_TAG, is_active=True)
        # The "You extract information..." preamble survives.
        self.assertIn("extract information", route.system_prompt.lower())

    def test_discipline_confidence_now_optional(self):
        # A discipline item with only a slug validates after seed change.
        from llm.schema import validate

        route = ModelRoute.objects.get(task=LLMTask.DISCIPLINE_TAG, is_active=True)
        payload = {"disciplines": [{"slug": "borehole"}]}
        validate(payload, route.json_schema)  # must not raise

    def test_entity_contact_without_name_now_validates(self):
        # A notice can name a role and phone without a personal name; that
        # contact is still useful, so full_name is no longer required.
        from llm.schema import validate

        route = ModelRoute.objects.get(task=LLMTask.ENTITY_ENRICH, is_active=True)
        payload = {"contacts": [{"title": "DCD", "phones": ["000"]}]}
        validate(payload, route.json_schema)  # must not raise
