"""
Point every LLM route at a single model.

Configuration is data (spec section 7): which model serves which task is an
admin edit, not a code change.  This command retargets all active ModelRoute
rows to one model_name in place -- it does not add rows, so re-running
seed_llm_routes afterwards will not resurrect the old targets as duplicates.

    python manage.py set_model gemma4:31b-cloud
    python manage.py set_model gemma4:31b-cloud --no-vision   # text-only model

By default every route is marked vision-capable, which is correct for a
multimodal model like gemma4 (Text + Image).  Pass --no-vision for a text-only
model; the DOC_EXTRACT task, which reads document images, will then be flagged
in the summary as unserviceable so the mismatch is visible rather than silent.
"""
from django.core.management.base import BaseCommand, CommandError

from core.enums import LLMTask
from llm.models import ModelRoute


class Command(BaseCommand):
    help = "Retarget every active LLM route to one model name."

    def add_arguments(self, parser):
        parser.add_argument("model_name", help="e.g. gemma4:31b-cloud")
        parser.add_argument(
            "--no-vision", action="store_true",
            help="Mark routes as text-only (a text model cannot serve DOC_EXTRACT).",
        )

    def handle(self, *args, **options):
        model_name = options["model_name"].strip()
        if not model_name:
            raise CommandError("model_name cannot be empty")
        vision = not options["no_vision"]

        routes = ModelRoute.objects.filter(is_active=True)
        if not routes.exists():
            raise CommandError(
                "no active routes to retarget. Seed first: python manage.py seed_llm_routes"
            )

        updated = 0
        for route in routes:
            route.model_name = model_name
            route.supports_vision = vision
            route.save(update_fields=["model_name", "supports_vision"])
            self.stdout.write(f"  {route.task.ljust(16)} -> {model_name}")
            updated += 1

        # Surface a vision mismatch rather than letting DOC_EXTRACT fail silently.
        if not vision:
            doc = ModelRoute.objects.filter(
                task=LLMTask.DOC_EXTRACT, is_active=True
            ).exists()
            if doc:
                self.stdout.write(self.style.WARNING(
                    "DOC_EXTRACT reads document images but the model is marked "
                    "text-only. That task will not work until a vision model serves it."
                ))

        self.stdout.write(self.style.SUCCESS(f"{updated} route(s) now point at {model_name}"))
