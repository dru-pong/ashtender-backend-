"""
Gateway tests.

The network is never touched: ``llm.gateway._transport`` exists as a single
seam precisely so it can be replaced here.  If a test in this file starts
making real HTTP calls, that seam has been bypassed.
"""
import json
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

import requests

from core.enums import LLMTask
from core.tests.factories import make_org
from llm import gateway
from llm.models import CircuitState, LLMCall, ModelRoute
from llm.schema import SchemaError, validate

SIMPLE_SCHEMA = {
    "type": "object",
    "required": ["doc_type"],
    "properties": {"doc_type": {"type": "string"}, "confidence": {"type": "number"}},
}


def provider_reply(payload, prompt_tokens=100, completion_tokens=20):
    """Shape of an OpenAI-compatible chat completion."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


class GatewayTestCase(TestCase):
    def setUp(self):
        self.org = make_org()
        self.route = ModelRoute.objects.create(
            task=LLMTask.DOC_CLASSIFY,
            model_name="test-model",
            json_schema=SIMPLE_SCHEMA,
            cost_per_1k_input="0.001000",
            cost_per_1k_output="0.002000",
            system_prompt="You extract information.",
        )
        CircuitState.objects.all().delete()


class RoutingTests(GatewayTestCase):
    def test_highest_priority_active_route_wins(self):
        ModelRoute.objects.create(
            task=LLMTask.DOC_CLASSIFY, model_name="better-model", priority=50
        )
        self.assertEqual(gateway.get_route(LLMTask.DOC_CLASSIFY).model_name, "better-model")

    def test_inactive_routes_are_ignored(self):
        self.route.is_active = False
        self.route.save()
        with self.assertRaises(gateway.NoRouteConfigured):
            gateway.get_route(LLMTask.DOC_CLASSIFY)

    def test_missing_route_names_the_seed_command(self):
        with self.assertRaises(gateway.NoRouteConfigured) as raised:
            gateway.get_route(LLMTask.NARRATIVE)
        self.assertIn("seed_llm_routes", str(raised.exception))


class HappyPathTests(GatewayTestCase):
    def test_valid_response_is_logged_with_tokens_and_cost(self):
        from decimal import Decimal

        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "TAX_CLEARANCE"})
        ):
            result = gateway.complete(
                LLMTask.DOC_CLASSIFY, "what is this?", organization=self.org
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["doc_type"], "TAX_CLEARANCE")
        call = result.call
        self.assertEqual(call.status, LLMCall.Status.OK)
        self.assertEqual(call.organization_id, self.org.pk)
        self.assertEqual(call.tokens_in, 100)
        self.assertEqual(call.tokens_out, 20)
        # 100/1000 * 0.001 + 20/1000 * 0.002
        self.assertEqual(call.cost_usd, Decimal("0.000140"))

    def test_catalogue_work_logs_with_no_organization(self):
        """Mining the OCDS corpus belongs to no tenant; a fake org would corrupt
        exactly the cost attribution this table exists for."""
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ):
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "corpus mining")
        self.assertIsNone(result.call.organization_id)

    def test_code_fences_are_stripped(self):
        fenced = '```json\n{"doc_type": "SSNIT_CLEARANCE"}\n```'
        with mock.patch.object(gateway, "_transport", return_value=provider_reply(fenced)):
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["doc_type"], "SSNIT_CLEARANCE")

    def test_prompt_is_only_stored_truncated(self):
        """Prompts can carry another firm's audited accounts."""
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ):
            result = gateway.complete(
                LLMTask.DOC_CLASSIFY, "y" * 5000, organization=self.org
            )
        self.assertLessEqual(len(result.call.prompt_preview), 500)


class CacheTests(GatewayTestCase):
    def test_identical_work_is_served_from_cache_without_a_second_call(self):
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "TAX_CLEARANCE"})
        ) as transport:
            gateway.complete(LLMTask.DOC_CLASSIFY, "same prompt", organization=self.org)
            second = gateway.complete(LLMTask.DOC_CLASSIFY, "same prompt", organization=self.org)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(second.status, LLMCall.Status.CACHED)
        self.assertEqual(second.data["doc_type"], "TAX_CLEARANCE")

    def test_a_cache_hit_costs_nothing(self):
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ):
            gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
            second = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(second.call.cost_usd, 0)
        self.assertEqual(second.call.tokens_in, 0)

    def test_a_different_prompt_is_not_a_cache_hit(self):
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ) as transport:
            gateway.complete(LLMTask.DOC_CLASSIFY, "prompt one", organization=self.org)
            gateway.complete(LLMTask.DOC_CLASSIFY, "prompt two", organization=self.org)
        self.assertEqual(transport.call_count, 2)

    def test_the_cache_is_shared_across_tenants_because_the_input_is_the_input(self):
        """
        The hash covers task, model and payload. Two tenants extracting the
        same public tender text get the same answer, and paying twice for it
        would be a pure loss. Nothing tenant-specific is in the key, so
        nothing tenant-specific can leak through it.
        """
        other = make_org("Other Contractor Ltd", "other")
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ) as transport:
            gateway.complete(LLMTask.DOC_CLASSIFY, "public notice text", organization=self.org)
            second = gateway.complete(
                LLMTask.DOC_CLASSIFY, "public notice text", organization=other
            )
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(second.call.organization_id, other.pk)

    @override_settings(LLM_CACHE_ENABLED=False)
    def test_cache_can_be_disabled(self):
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ) as transport:
            gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
            gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(transport.call_count, 2)


class SchemaRepairTests(GatewayTestCase):
    def test_one_repair_attempt_is_made_and_can_succeed(self):
        replies = [
            provider_reply("this is not json at all"),
            provider_reply({"doc_type": "TAX_CLEARANCE"}),
        ]
        with mock.patch.object(gateway, "_transport", side_effect=replies) as transport:
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(result.status, LLMCall.Status.SCHEMA_REPAIRED)
        self.assertTrue(result.ok)

    def test_a_second_failure_hard_fails_rather_than_a_third_guess(self):
        """A second guess at a broken response is a slower way to be wrong."""
        replies = [provider_reply("nonsense"), provider_reply("still nonsense")]
        with mock.patch.object(gateway, "_transport", side_effect=replies) as transport:
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(result.status, LLMCall.Status.SCHEMA_FAILED)
        self.assertFalse(result.ok)
        self.assertIsNone(result.data)

    def test_valid_json_that_violates_the_schema_is_a_failure(self):
        replies = [
            provider_reply({"confidence": 0.9}),
            provider_reply({"confidence": 0.9}),
        ]
        with mock.patch.object(gateway, "_transport", side_effect=replies):
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(result.status, LLMCall.Status.SCHEMA_FAILED)
        self.assertIn("doc_type", result.error)

    def test_tokens_from_both_attempts_are_billed(self):
        replies = [provider_reply("nonsense"), provider_reply({"doc_type": "X"})]
        with mock.patch.object(gateway, "_transport", side_effect=replies):
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(result.call.tokens_in, 200)
        self.assertEqual(result.call.attempt_count, 2)

    def test_a_schema_failure_does_not_open_the_circuit(self):
        """The provider answered. The model was wrong, which is a different fault."""
        replies = [provider_reply("nonsense"), provider_reply("nonsense")]
        with mock.patch.object(gateway, "_transport", side_effect=replies):
            gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertFalse(gateway.circuit_is_open("test-model"))


class FailureAndCircuitTests(GatewayTestCase):
    def test_a_provider_error_returns_a_result_instead_of_raising(self):
        """
        Every caller's correct response to a failure is the same: fall back to
        UNKNOWN and queue the work. Raising would push that into a dozen
        try blocks.
        """
        with mock.patch.object(
            gateway, "_transport", side_effect=requests.HTTPError("502 Bad Gateway")
        ):
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(result.status, LLMCall.Status.PROVIDER_ERROR)
        self.assertFalse(result.ok)
        self.assertIsNone(result.unwrap())

    def test_unwrap_returns_the_supplied_default_on_failure(self):
        with mock.patch.object(gateway, "_transport", side_effect=requests.HTTPError("500")):
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(result.unwrap({"fields": []}), {"fields": []})

    def test_a_timeout_is_recorded_distinctly(self):
        with mock.patch.object(gateway, "_transport", side_effect=requests.Timeout("slow")):
            result = gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(result.status, LLMCall.Status.TIMEOUT)

    @override_settings(LLM_CIRCUIT_FAIL_THRESHOLD=3)
    def test_the_circuit_opens_after_the_threshold(self):
        with mock.patch.object(gateway, "_transport", side_effect=requests.HTTPError("500")):
            for _ in range(3):
                gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org, cache=False)
        self.assertTrue(gateway.circuit_is_open("test-model"))

    @override_settings(LLM_CIRCUIT_FAIL_THRESHOLD=2)
    def test_an_open_circuit_skips_the_provider_entirely(self):
        with mock.patch.object(gateway, "_transport", side_effect=requests.HTTPError("500")):
            for _ in range(2):
                gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org, cache=False)
        with mock.patch.object(gateway, "_transport") as transport:
            result = gateway.complete(
                LLMTask.DOC_CLASSIFY, "p", organization=self.org, cache=False
            )
        transport.assert_not_called()
        self.assertEqual(result.status, LLMCall.Status.CIRCUIT_OPEN)

    @override_settings(LLM_CIRCUIT_FAIL_THRESHOLD=2, LLM_CIRCUIT_RESET_SECONDS=0)
    def test_the_circuit_half_opens_to_let_one_probe_through(self):
        """Recovery must not need a human."""
        with mock.patch.object(gateway, "_transport", side_effect=requests.HTTPError("500")):
            for _ in range(2):
                gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org, cache=False)
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ):
            result = gateway.complete(
                LLMTask.DOC_CLASSIFY, "p", organization=self.org, cache=False
            )
        self.assertTrue(result.ok)
        self.assertFalse(gateway.circuit_is_open("test-model"))

    def test_a_success_resets_the_failure_count(self):
        with mock.patch.object(gateway, "_transport", side_effect=requests.HTTPError("500")):
            gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org, cache=False)
        self.assertEqual(CircuitState.objects.get(model_name="test-model").consecutive_failures, 1)
        with mock.patch.object(
            gateway, "_transport", return_value=provider_reply({"doc_type": "X"})
        ):
            gateway.complete(LLMTask.DOC_CLASSIFY, "p2", organization=self.org, cache=False)
        self.assertEqual(CircuitState.objects.get(model_name="test-model").consecutive_failures, 0)


class VisionPayloadTests(GatewayTestCase):
    def test_images_are_sent_as_content_parts(self):
        captured = {}

        def fake_transport(url, headers, body, timeout):
            captured["body"] = body
            return provider_reply({"doc_type": "X"})

        with mock.patch.object(gateway, "_transport", side_effect=fake_transport):
            gateway.complete(
                LLMTask.DOC_CLASSIFY, "read this page",
                organization=self.org, images=["data:image/png;base64,AAA"],
            )
        content = captured["body"]["messages"][-1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")

    def test_the_system_prompt_leads_the_message_list(self):
        captured = {}

        def fake_transport(url, headers, body, timeout):
            captured["body"] = body
            return provider_reply({"doc_type": "X"})

        with mock.patch.object(gateway, "_transport", side_effect=fake_transport):
            gateway.complete(LLMTask.DOC_CLASSIFY, "p", organization=self.org)
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")


class ContentHashTests(TestCase):
    def test_the_hash_is_stable_and_order_independent(self):
        first = gateway.content_hash("T", "m", {"a": 1, "b": 2})
        second = gateway.content_hash("T", "m", {"b": 2, "a": 1})
        self.assertEqual(first, second)

    def test_the_hash_covers_the_model(self):
        self.assertNotEqual(
            gateway.content_hash("T", "model-a", {"p": 1}),
            gateway.content_hash("T", "model-b", {"p": 1}),
        )

    def test_the_hash_covers_the_task(self):
        self.assertNotEqual(
            gateway.content_hash("TASK_A", "m", {"p": 1}),
            gateway.content_hash("TASK_B", "m", {"p": 1}),
        )


class GroundedExtractionSchemaTests(TestCase):
    """A value with no bounding box cannot be confirmed against a crop."""

    def test_a_field_without_a_bbox_is_rejected(self):
        from llm.schema import GROUNDED_EXTRACTION_SCHEMA

        with self.assertRaises(SchemaError):
            validate(
                {"fields": [{"field": "expires_on", "value": "2026-11-30", "confidence": 0.9}]},
                GROUNDED_EXTRACTION_SCHEMA,
            )

    def test_a_truncated_bbox_is_rejected(self):
        from llm.schema import GROUNDED_EXTRACTION_SCHEMA

        with self.assertRaises(SchemaError):
            validate(
                {
                    "fields": [
                        {
                            "field": "expires_on", "value": "2026-11-30",
                            "bbox": [0.1, 0.2], "confidence": 0.9,
                        }
                    ]
                },
                GROUNDED_EXTRACTION_SCHEMA,
            )

    def test_a_complete_field_is_accepted(self):
        from llm.schema import GROUNDED_EXTRACTION_SCHEMA

        payload = {
            "fields": [
                {
                    "field": "expires_on", "value": "2026-11-30",
                    "bbox": [0.61, 0.44, 0.88, 0.49], "confidence": 0.82,
                }
            ]
        }
        self.assertIs(validate(payload, GROUNDED_EXTRACTION_SCHEMA), payload)

    def test_a_boolean_where_a_number_is_expected_is_rejected(self):
        with self.assertRaises(SchemaError):
            validate({"confidence": True}, {
                "type": "object",
                "properties": {"confidence": {"type": "number"}},
            })
