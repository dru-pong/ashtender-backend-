"""
Render an uploaded document's pages to images.

This is the pipeline the handoff (section 4.4) names as blocking every
extraction: ``llm.tasks.extract_document_fields`` needs ``DocumentPage.image``
rows, and nothing was creating them outside tests.

Design choices, all matching constraints in the brief:

*   **Local and free.**  PDFs are rasterised with poppler's ``pdftoppm``, which
    is a tiny system dependency, rather than a paid rendering API.  Images are
    handled with Pillow (already pinned for ``ImageField``).  No cloud service
    touches the document, which also keeps the cross-border-transfer surface
    (Act 843) limited to the one hosted model call the gateway already declares.
*   **Resolution is capped.**  The VPS is low-RAM and a vision model does not
    need 600dpi, so rendering is at a modest DPI and the longest edge is capped.
*   **The subprocess call is isolated** in ``_rasterise_pdf`` so tests replace
    exactly that, the same way the LLM gateway and the harvester isolate their
    transports.

Rendering never sets ``verified_by_human``; it only produces the crops a human
later confirms against.  It also does not call the model -- queuing extraction
is the caller's job, so the render step stays pure I/O.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

#: Modest DPI for a low-RAM VPS.  Enough for a vision model to read dates and
#: certificate numbers; far below what would blow the memory budget.
DEFAULT_DPI = 150
#: Cap on how many pages we bother rendering.  Compliance documents are short,
#: and the extraction task only reads the first few pages anyway.
MAX_PAGES = 8
#: Longest-edge cap in pixels, applied after rasterisation.
MAX_EDGE = 2000

PDF_MIME_HINTS = ("application/pdf", ".pdf")
IMAGE_MIME_HINTS = ("image/", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")


def _looks_like_pdf(document) -> bool:
    name = (document.file.name or "").lower()
    mime = (document.mime_type or "").lower()
    return "pdf" in mime or name.endswith(".pdf")


def _looks_like_image(document) -> bool:
    name = (document.file.name or "").lower()
    mime = (document.mime_type or "").lower()
    return mime.startswith("image/") or name.endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")
    )


def _rasterise_pdf(pdf_path: str, out_dir: str, dpi: int = DEFAULT_DPI) -> list[str]:
    """
    Rasterise a PDF to PNG pages with poppler's pdftoppm.

    Isolated so tests can replace it without a real PDF or poppler install.
    Returns the sorted list of produced PNG paths.  Raises FileNotFoundError if
    poppler is not installed, which the caller turns into a recorded, non-fatal
    condition rather than a crash.
    """
    prefix = os.path.join(out_dir, "page")
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), "-l", str(MAX_PAGES), pdf_path, prefix],
        check=True,
        capture_output=True,
    )
    produced = sorted(
        os.path.join(out_dir, name)
        for name in os.listdir(out_dir)
        if name.startswith("page") and name.endswith(".png")
    )
    return produced[:MAX_PAGES]


def _downscale(png_path: str) -> tuple[int, int]:
    """Cap the longest edge in place; return final (width, height)."""
    from PIL import Image

    with Image.open(png_path) as img:
        width, height = img.size
        longest = max(width, height)
        if longest > MAX_EDGE:
            scale = MAX_EDGE / longest
            width, height = int(width * scale), int(height * scale)
            img = img.resize((width, height))
            img.save(png_path)
        return width, height


def render_document_pages(document, *, dpi: int = DEFAULT_DPI) -> int:
    """
    Render ``document`` into ``DocumentPage`` rows and return the page count.

    Idempotent: existing pages for the document are cleared first, so a
    re-render after a better upload does not leave stale crops behind.  A PDF
    with no readable pages, or a missing poppler binary, returns 0 and logs --
    the extraction task already handles "no pages rendered" as UNKNOWN-inducing
    rather than an error.
    """
    from django.core.files.base import ContentFile

    from vault.models import DocumentPage

    if not document.file:
        return 0

    is_pdf = _looks_like_pdf(document)
    is_image = _looks_like_image(document)
    if not (is_pdf or is_image):
        logger.info("document %s: unrenderable type, skipping", document.pk)
        return 0

    document.pages.all().delete()

    with tempfile.TemporaryDirectory() as work_dir:
        # Pull the stored file down to a local temp path (storage may be remote).
        local_path = os.path.join(work_dir, os.path.basename(document.file.name) or "doc")
        document.file.open("rb")
        try:
            with open(local_path, "wb") as handle:
                for chunk in document.file.chunks():
                    handle.write(chunk)
        finally:
            document.file.close()

        try:
            if is_pdf:
                page_paths = _rasterise_pdf(local_path, work_dir, dpi=dpi)
            else:
                page_paths = [local_path]
        except FileNotFoundError:
            logger.warning("poppler (pdftoppm) not installed; cannot render %s", document.pk)
            return 0
        except subprocess.CalledProcessError as exc:
            logger.warning("rasterise failed for %s: %s", document.pk, exc)
            return 0

        written = 0
        for index, page_path in enumerate(page_paths, start=1):
            try:
                width, height = _downscale(page_path)
            except Exception:  # Pillow could not read it
                width = height = None
            with open(page_path, "rb") as handle:
                content = handle.read()
            page = DocumentPage(
                organization_id=document.organization_id,
                document=document,
                page_number=index,
                width=width,
                height=height,
            )
            page.image.save(f"doc{document.pk}_p{index}.png", ContentFile(content), save=True)
            written += 1

    if written:
        document.page_count = written
        document.save(update_fields=["page_count"])
    return written
