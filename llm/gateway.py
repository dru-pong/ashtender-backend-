"""
The single door to the model provider.

Nothing in AshTender calls an inference endpoint directly.  Everything comes
through ``complete()``, which guarantees five properties the rest of the
system relies on:

1.  **Every call is logged** against a tenant with token counts and a cost, so
    per-tenant unit economics exist before anybody sets a price.
2.  **Identical work is free.**  The content hash covers task, model and
    payload, so re-extracting an unchanged document is a cache read.
3.  **Malformed JSON gets exactly one repair attempt**, then hard-fails.  A
    second guess at a broken response is a slower way to be wrong.
4.  **A provider outage degrades, it does not 500.**  The breaker opens and
    callers receive a failed result to convert into UNKNOWN.
5.  **The model never returns a verdict.**  This module returns extracted
    data; eligibility decisions are computed in Python from that data.  See
    the invariant test in eligibility/tests/test_invariants.py.

Sending document images to a hosted provider is a cross-border transfer of
personal data and must be declared to the Ghana Data Protection Commission at
registration (Act 843 s.27(1)).  ``LLM_PROVIDER_JURISDICTION`` records where.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone

from llm.models import CircuitState, LLMCall, ModelRoute
from llm.schema import SchemaError, validate

logger = logging.getLogger(__name__)

REPAIR_INSTRUCTION = (
    "Your previous reply was not valid JSON matching the required schema. "
    "Reply with the corrected JSON object only. No prose, no code fences."
)


class NoRouteConfigured(Exception):
    """No active ModelRoute for a task.  Configuration bug, not a runtime one."""


@dataclass
class LLMResult:
    """Outcome of one logical call, cache hits included."""

    status: str
    data: dict | None = None
    call: LLMCall | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (LLMCall.Status.OK, LLMCall.Status.CACHED,
                               LLMCall.Status.SCHEMA_REPAIRED)

    def unwrap(self, default=None):
        return self.data if self.ok and self.data is not None else default


def content_hash(task: str, model_name: str, payload) -> str:
    blob = json.dumps(
        {"task": task, "model": model_name, "payload": payload},
        sort_keys=True, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get_route(task: str) -> ModelRoute:
    route = ModelRoute.objects.filter(task=task, is_active=True).order_by("-priority").first()
    if route is None:
        raise NoRouteConfigured(
            f"No active ModelRoute for task {task!r}. Seed one: "
            "python manage.py seed_llm_routes"
        )
    return route


# --- circuit breaker ----------------------------------------------------
def circuit_is_open(model_name: str) -> bool:
    state = CircuitState.objects.filter(model_name=model_name).first()
    if state is None or state.opened_at is None:
        return False
    reset_after = settings.LLM_CIRCUIT_RESET_SECONDS
    if (timezone.now() - state.opened_at).total_seconds() >= reset_after:
        # Half-open: let one probe through rather than waiting for a human.
        CircuitState.objects.filter(pk=state.pk).update(
            opened_at=None, consecutive_failures=0
        )
        return False
    return True


def record_failure(model_name: str, error: str) -> None:
    state, _ = CircuitState.objects.get_or_create(model_name=model_name)
    state.consecutive_failures += 1
    state.last_failure_at = timezone.now()
    state.last_error = error[:2000]
    if state.consecutive_failures >= settings.LLM_CIRCUIT_FAIL_THRESHOLD and not state.opened_at:
        state.opened_at = timezone.now()
        logger.error(
            "LLM circuit opened for %s after %s consecutive failures",
            model_name, state.consecutive_failures,
        )
    state.save(update_fields=["consecutive_failures", "last_failure_at", "last_error", "opened_at"])


def record_success(model_name: str) -> None:
    CircuitState.objects.filter(model_name=model_name).update(
        consecutive_failures=0, opened_at=None
    )


# --- transport ----------------------------------------------------------
def _transport(url: str, headers: dict, body: dict, timeout: int) -> dict:
    """
    The only place an HTTP request is made.

    Separated so tests replace this one function instead of monkeypatching
    requests globally, which would hide real network calls made by accident.
    """
    response = requests.post(url, headers=headers, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _build_messages(route: ModelRoute, prompt: str, images=None) -> list:
    messages = []
    if route.system_prompt:
        messages.append({"role": "system", "content": route.system_prompt})
    if images:
        content = [{"type": "text", "text": prompt}]
        for image in images:
            content.append(
                {"type": "image_url", "image_url": {"url": image}}
            )
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})
    return messages


def _extract_text(response: dict) -> str:
    try:
        return response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _normalise_to_schema(parsed, schema: dict):
    """
    Reshape a model's JSON toward what the schema expects, absorbing the
    harmless shape differences every provider has, in one place.

    Two adjustments, both conservative -- they only rewrap/rename, never invent
    data:

    *   **Bare array -> wrapped object.**  If the schema is an object whose only
        required property is an array (``{"works": [...]}``, ``{"fields": [...]}``)
        and the model returned a bare ``[...]``, wrap the list under that
        property.  This is the Gemma-4 case: it answers "list the works" with a
        top-level array rather than an object.

    *   **Alternate item keys.**  Models pick their own natural key names
        (``item`` for ``description``, ``name`` for ``full_name``).  Where an
        item object is missing a required key but carries a well-known synonym,
        copy it across.  Unknown shapes are left untouched for validate() to
        reject, so a genuinely wrong response still fails loudly.
    """
    if not isinstance(schema, dict):
        return parsed

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    # 1. Bare array -> wrap under the single required array property.
    if isinstance(parsed, list) and len(required) == 1:
        key = required[0]
        if properties.get(key, {}).get("type") == "array":
            parsed = {key: parsed}

    if not isinstance(parsed, dict):
        return parsed

    # 2. Rename alternate item keys within each array property.
    for key, prop_schema in properties.items():
        if prop_schema.get("type") != "array" or key not in parsed:
            continue
        items = parsed[key]
        if not isinstance(items, list):
            continue
        item_schema = prop_schema.get("items", {})
        item_required = item_schema.get("required", [])
        item_props = item_schema.get("properties", {})
        for index, entry in enumerate(items):
            # Bare string where an object is expected: promote it under the
            # first required string key (e.g. ["borehole"] -> [{"slug": ...}]).
            if isinstance(entry, str) and item_schema.get("type") == "object":
                promoted = None
                for target in item_required:
                    if item_props.get(target, {}).get("type") == "string":
                        promoted = {target: entry}
                        break
                if promoted is not None:
                    items[index] = entry = promoted
            if not isinstance(entry, dict):
                continue
            for target in item_required:
                if target in entry:
                    continue
                for synonym in _KEY_SYNONYMS.get(target, ()):
                    if synonym in entry:
                        entry[target] = entry[synonym]
                        break
    return parsed


#: Well-known synonyms models use for our required item keys. Only used to fill
#: a missing required key from a present one; never to overwrite or invent.
_KEY_SYNONYMS = {
    "description": ("item", "work", "name", "title", "text"),
    "full_name": ("name", "fullName", "full_name", "person", "contact_name"),
    "slug": ("discipline", "code", "id", "label", "name"),
    "field": ("field_name", "name", "key", "label"),
    "value": ("val", "text", "content"),
    "source_text": ("sentence", "quote", "source", "text"),
    "doc_type": ("type", "document_type", "code"),
}


def _parse_json(text: str):
    """
    Strip fences a model added despite being told not to, then parse.

    Handles both a top-level object ``{...}`` and a top-level array ``[...]``.
    Some models (Gemma 4, for one) answer "list the works" with a bare array
    rather than wrapping it in an object, which is reasonable -- so we slice to
    whichever of ``{`` / ``[`` comes first and parse that, instead of assuming
    an object and choking on "Extra data".
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip().strip("`").strip()

    obj_start, arr_start = cleaned.find("{"), cleaned.find("[")
    starts = [s for s in (obj_start, arr_start) if s != -1]
    if starts:
        start = min(starts)
        if cleaned[start] == "[":
            end = cleaned.rfind("]")
        else:
            end = cleaned.rfind("}")
        if end > start:
            cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def _usage(response: dict) -> tuple[int, int]:
    usage = response.get("usage") or {}
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _cost(route: ModelRoute, tokens_in: int, tokens_out: int) -> Decimal:
    return (
        Decimal(tokens_in) / Decimal(1000) * route.cost_per_1k_input
        + Decimal(tokens_out) / Decimal(1000) * route.cost_per_1k_output
    ).quantize(Decimal("0.000001"))


# --- public entry point -------------------------------------------------
def complete(task: str, prompt: str, *, organization=None, images=None,
             route: ModelRoute | None = None, cache: bool | None = None,
             payload_extra=None) -> LLMResult:
    """
    Run one task through the gateway.

    Returns an ``LLMResult`` rather than raising, because every caller's
    correct response to a failure is the same: fall back to UNKNOWN and queue
    the work.  Raising would push that decision into a dozen try blocks.
    """
    route = route or get_route(task)
    org_id = getattr(organization, "pk", organization)
    use_cache = settings.LLM_CACHE_ENABLED if cache is None else cache

    payload = {"prompt": prompt, "images": list(images or []), "extra": payload_extra}
    chash = content_hash(task, route.model_name, payload)

    if use_cache:
        hit = (
            LLMCall.objects.filter(
                content_hash=chash, task=task,
                status__in=[LLMCall.Status.OK, LLMCall.Status.CACHED,
                            LLMCall.Status.SCHEMA_REPAIRED],
            )
            .order_by("-created_at")
            .first()
        )
        if hit is not None:
            cached = LLMCall.objects.create(
                organization_id=org_id, task=task, route=route,
                model_name=route.model_name, status=LLMCall.Status.CACHED,
                content_hash=chash, tokens_in=0, tokens_out=0, cost_usd=0,
                response=hit.response, attempt_count=0,
                prompt_preview=prompt[:500],
            )
            return LLMResult(status=LLMCall.Status.CACHED, data=hit.response, call=cached)

    if circuit_is_open(route.model_name):
        call = LLMCall.objects.create(
            organization_id=org_id, task=task, route=route, model_name=route.model_name,
            status=LLMCall.Status.CIRCUIT_OPEN, content_hash=chash,
            error="circuit open", attempt_count=0, prompt_preview=prompt[:500],
        )
        return LLMResult(status=LLMCall.Status.CIRCUIT_OPEN, call=call,
                         error="LLM provider circuit is open")

    url = settings.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    messages = _build_messages(route, prompt, images)
    tokens_in = tokens_out = 0
    attempts = 0
    started = time.monotonic()
    last_error = ""
    raw_text = ""

    # One real attempt, then at most one repair.  Never a third.
    for attempts in (1, 2):
        body = {
            "model": route.model_name,
            "messages": messages,
            "temperature": route.temperature,
            "max_tokens": route.max_tokens,
        }
        if route.json_schema:
            body["response_format"] = {"type": "json_object"}
        try:
            response = _transport(url, headers, body, settings.LLM_TIMEOUT_SECONDS)
        except requests.Timeout as exc:
            record_failure(route.model_name, f"timeout: {exc}")
            call = LLMCall.objects.create(
                organization_id=org_id, task=task, route=route,
                model_name=route.model_name, status=LLMCall.Status.TIMEOUT,
                content_hash=chash, error=str(exc)[:2000], attempt_count=attempts,
                latency_ms=int((time.monotonic() - started) * 1000),
                prompt_preview=prompt[:500],
            )
            return LLMResult(status=LLMCall.Status.TIMEOUT, call=call, error=str(exc))
        except Exception as exc:  # provider 5xx, connection reset, bad gateway
            record_failure(route.model_name, f"{type(exc).__name__}: {exc}")
            call = LLMCall.objects.create(
                organization_id=org_id, task=task, route=route,
                model_name=route.model_name, status=LLMCall.Status.PROVIDER_ERROR,
                content_hash=chash, error=f"{type(exc).__name__}: {exc}"[:2000],
                attempt_count=attempts,
                latency_ms=int((time.monotonic() - started) * 1000),
                prompt_preview=prompt[:500],
            )
            return LLMResult(status=LLMCall.Status.PROVIDER_ERROR, call=call, error=str(exc))

        delta_in, delta_out = _usage(response)
        tokens_in += delta_in
        tokens_out += delta_out
        raw_text = _extract_text(response)

        try:
            parsed = _parse_json(raw_text) if route.json_schema else {"text": raw_text}
            if route.json_schema:
                parsed = _normalise_to_schema(parsed, route.json_schema)
                validate(parsed, route.json_schema)
        except (json.JSONDecodeError, SchemaError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempts == 1:
                messages = messages + [
                    {"role": "assistant", "content": raw_text[:4000]},
                    {"role": "user", "content": f"{REPAIR_INSTRUCTION}\nError: {exc}"},
                ]
                continue
            record_success(route.model_name)  # provider answered; the model was wrong
            call = LLMCall.objects.create(
                organization_id=org_id, task=task, route=route,
                model_name=route.model_name, status=LLMCall.Status.SCHEMA_FAILED,
                content_hash=chash, tokens_in=tokens_in, tokens_out=tokens_out,
                cost_usd=_cost(route, tokens_in, tokens_out), error=last_error[:2000],
                attempt_count=attempts,
                latency_ms=int((time.monotonic() - started) * 1000),
                prompt_preview=prompt[:500],
            )
            return LLMResult(status=LLMCall.Status.SCHEMA_FAILED, call=call, error=last_error)

        record_success(route.model_name)
        status = (
            LLMCall.Status.SCHEMA_REPAIRED if attempts == 2 else LLMCall.Status.OK
        )
        call = LLMCall.objects.create(
            organization_id=org_id, task=task, route=route, model_name=route.model_name,
            status=status, content_hash=chash, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=_cost(route, tokens_in, tokens_out), response=parsed,
            attempt_count=attempts,
            latency_ms=int((time.monotonic() - started) * 1000),
            prompt_preview=prompt[:500],
        )
        return LLMResult(status=status, data=parsed, call=call)

    return LLMResult(status=LLMCall.Status.SCHEMA_FAILED, error=last_error or "unreachable")
