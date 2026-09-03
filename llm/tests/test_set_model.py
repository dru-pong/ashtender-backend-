"""set_model retargets every active route to one model, idempotently."""
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from core.enums import LLMTask
from llm.models import ModelRoute


class SetModelTests(TestCase):
    def setUp(self):
        ModelRoute.objects.create(
            task=LLMTask.DOC_EXTRACT, model_name="qwen2.5-vl:7b",
            is_active=True, priority=10, supports_vision=True,
        )
        ModelRoute.objects.create(
            task=LLMTask.TITLE_PARSE, model_name="qwen2.5:7b",
            is_active=True, priority=10, supports_vision=False,
        )

    def test_points_all_routes_at_one_model(self):
        call_command("set_model", "gemma4:31b-cloud", stdout=StringIO())
        models = set(ModelRoute.objects.values_list("model_name", flat=True))
        self.assertEqual(models, {"gemma4:31b-cloud"})

    def test_multimodal_by_default(self):
        call_command("set_model", "gemma4:31b-cloud", stdout=StringIO())
        self.assertTrue(all(r.supports_vision for r in ModelRoute.objects.all()))

    def test_no_vision_flag_marks_text_only(self):
        out = StringIO()
        call_command("set_model", "some-text-model", no_vision=True, stdout=out)
        self.assertFalse(any(r.supports_vision for r in ModelRoute.objects.all()))
        self.assertIn("DOC_EXTRACT", out.getvalue())

    def test_does_not_create_duplicate_rows(self):
        before = ModelRoute.objects.count()
        call_command("set_model", "gemma4:31b-cloud", stdout=StringIO())
        call_command("set_model", "gemma4:31b-cloud", stdout=StringIO())
        self.assertEqual(ModelRoute.objects.count(), before)

    def test_empty_model_name_rejected(self):
        with self.assertRaises(CommandError):
            call_command("set_model", "   ", stdout=StringIO())

    def test_no_routes_errors_clearly(self):
        ModelRoute.objects.all().delete()
        with self.assertRaises(CommandError):
            call_command("set_model", "gemma4:31b-cloud", stdout=StringIO())
