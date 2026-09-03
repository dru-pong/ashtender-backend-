"""
Smoke-test the LLM gateway against the configured provider, and set route costs.

The handoff (4.6) calls this out as small but load-bearing: every gateway test
mocks ``_transport``, so the OpenAI-compatible request/response shape has never
actually been verified against a real provider.  A wrong response shape breaks
every extraction task at once, so it is worth one real call per task type before
anything depends on it.

Two independent jobs, either or both:

*   ``--probe`` sends one minimal real prompt per active route through
    ``complete()`` and reports status, latency, and whether the response parsed.
    This makes a real network call, so it is an operator command, not a test.
*   ``--set-costs '{"DOC_EXTRACT": [0.0002, 0.0006], ...}'`` writes the real
    per-1k input/output USD figures onto each task's active route.  They ship at
    zero, which means the LLMCall ledger reports every extraction as free -- the
    one number that must not be wrong before a success fee is priced.  This half
    touches no network and is unit-tested.

Example:

    python manage.py smoke_llm --probe
    python manage.py smoke_llm --set-costs '{"DOC_EXTRACT": [0.00018, 0.00054]}'
"""
import json
import time

from django.core.management.base import BaseCommand, CommandError

from core.enums import LLMTask
from llm.models import ModelRoute

#: Minimal prompts per task, just enough to elicit a schema-shaped reply.
PROBE_PROMPTS = {
    LLMTask.DOC_CLASSIFY: "This is a VAT registration certificate. Classify it.",
    LLMTask.REQ_EXTRACT: (
        "NOTICE: Tenderers shall submit a valid Tax Clearance Certificate. "
        "List the required documents with the sentence each came from."
    ),
    LLMTask.TITLE_PARSE: "Construction of 1No. 3-Unit Classroom Block",
    LLMTask.DISCIPLINE_TAG: (
        "Title: Drilling of 10No. Boreholes. Choose from slugs: borehole, road."
    ),
    LLMTask.ENTITY_ENRICH: (
        "For enquiries contact the District Coordinating Director, "
        "P.O. Box 1, Sunyani, Tel 000. Extract the officer contact details."
    ),
    LLMTask.NARRATIVE: "Draft one sentence stating the firm has five years' experience.",
}


def apply_costs(cost_map: dict) -> list[str]:
    """
    Set cost_per_1k_input/output on the active route for each named task.

    Pure of any network; returns a list of human-readable change lines. A task
    with no active route is reported rather than silently skipped.
    """
    changes = []
    for task, pair in cost_map.items():
        if task not in LLMTask.values:
            raise CommandError(f"unknown task {task!r}; known: {sorted(LLMTask.values)}")
        try:
            cost_in, cost_out = pair
        except (TypeError, ValueError):
            raise CommandError(f"cost for {task} must be a [input, output] pair")
        route = (
            ModelRoute.objects.filter(task=task, is_active=True)
            .order_by("-priority")
            .first()
        )
        if route is None:
            changes.append(f"{task}: no active route, skipped")
            continue
        route.cost_per_1k_input = cost_in
        route.cost_per_1k_output = cost_out
        route.save(update_fields=["cost_per_1k_input", "cost_per_1k_output"])
        changes.append(f"{task}: {route.model_name} -> in={cost_in} out={cost_out}")
    return changes


class Command(BaseCommand):
    help = "Probe the LLM provider per task and/or set per-1k route costs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--probe", action="store_true",
            help="Make one real call per active route (hits the provider).",
        )
        parser.add_argument(
            "--set-costs",
            help='JSON map task -> [input_per_1k, output_per_1k], e.g. '
            '\'{"DOC_EXTRACT": [0.0002, 0.0006]}\'.',
        )

    def handle(self, *args, **options):
        if not options.get("probe") and not options.get("set_costs"):
            raise CommandError("nothing to do: pass --probe and/or --set-costs")

        if options.get("set_costs"):
            try:
                cost_map = json.loads(options["set_costs"])
            except json.JSONDecodeError as exc:
                raise CommandError(f"--set-costs is not valid JSON: {exc}")
            for line in apply_costs(cost_map):
                self.stdout.write(f"  {line}")
            self.stdout.write(self.style.SUCCESS("costs updated"))

        if options.get("probe"):
            self._probe()

    def _probe(self):
        from llm.gateway import complete

        routes = ModelRoute.objects.filter(is_active=True).order_by("task", "-priority")
        seen_tasks = set()
        any_fail = False
        for route in routes:
            if route.task in seen_tasks:
                continue  # one probe per task, highest-priority route
            seen_tasks.add(route.task)
            prompt = PROBE_PROMPTS.get(route.task, "Return a minimal valid response.")
            started = time.monotonic()
            # cache=False so a probe is always a real call, not a cache read.
            result = complete(route.task, prompt, cache=False)
            elapsed = int((time.monotonic() - started) * 1000)
            ok = result.ok
            any_fail = any_fail or not ok
            marker = self.style.SUCCESS("ok") if ok else self.style.ERROR("FAIL")
            detail = "" if ok else f" ({result.status}: {result.error[:120]})"
            self.stdout.write(
                f"  {route.task.ljust(16)} {route.model_name.ljust(18)} "
                f"{marker} {elapsed}ms{detail}"
            )
        if any_fail:
            self.stdout.write(
                self.style.WARNING(
                    "at least one probe failed. Check LLM_BASE_URL, LLM_API_KEY, "
                    "and that the model names in seed_llm_routes exist on the provider."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("all probes ok"))
