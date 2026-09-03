"""
smoke_llm --set-costs.

Only the cost-setting half is unit-tested; --probe makes a real network call by
design and is an operator command. This is the number the handoff (4.6) flags as
the one that must not be wrong before pricing: costs ship at zero, so the ledger
reports every extraction as free until these are set.
"""
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from core.enums import LLMTask
from llm.management.commands.smoke_llm import apply_costs
from llm.models import ModelRoute


class SetCostsTests(TestCase):
    def setUp(self):
        self.route = ModelRoute.objects.create(
            task=LLMTask.DOC_EXTRACT, model_name="qwen2.5-vl:7b",
            is_active=True, priority=10,
        )

    def test_sets_costs_on_active_route(self):
        apply_costs({"DOC_EXTRACT": [0.0002, 0.0006]})
        self.route.refresh_from_db()
        self.assertEqual(self.route.cost_per_1k_input, Decimal("0.000200"))
        self.assertEqual(self.route.cost_per_1k_output, Decimal("0.000600"))

    def test_unknown_task_is_rejected(self):
        with self.assertRaises(CommandError):
            apply_costs({"NOT_A_TASK": [0.1, 0.2]})

    def test_bad_pair_is_rejected(self):
        with self.assertRaises(CommandError):
            apply_costs({"DOC_EXTRACT": [0.1]})

    def test_task_without_route_is_reported_not_crashing(self):
        lines = apply_costs({"NARRATIVE": [0.1, 0.2]})
        self.assertTrue(any("no active route" in line for line in lines))

    def test_command_invocation(self):
        out = StringIO()
        call_command(
            "smoke_llm", set_costs='{"DOC_EXTRACT": [0.00018, 0.00054]}', stdout=out
        )
        self.route.refresh_from_db()
        self.assertEqual(self.route.cost_per_1k_input, Decimal("0.000180"))
        self.assertIn("costs updated", out.getvalue())

    def test_command_needs_something_to_do(self):
        with self.assertRaises(CommandError):
            call_command("smoke_llm")
