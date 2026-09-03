"""
Document rendering + upload processing pipeline.

The happy-path render test builds a real one-page PDF with Pillow and rasterises
it with poppler, so the pipeline is exercised end to end rather than mocked into
tautology. The poppler-missing case is simulated so the graceful-degradation
branch is covered too.
"""
import io
import tempfile
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from core.tests.factories import make_org
from vault.models import Document, DocumentPage

_MEDIA = tempfile.mkdtemp(prefix="ashtender-test-media-")


def _pdf_bytes(pages=1):
    from PIL import Image

    imgs = [Image.new("RGB", (600, 800), "white") for _ in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    return buf.getvalue()


def _make_document(org, filename, content):
    doc = Document(organization=org, title="Cert", mime_type="")
    doc.file.save(filename, ContentFile(content), save=True)
    return doc


@override_settings(MEDIA_ROOT=_MEDIA)
class RenderPdfTests(TestCase):
    def setUp(self):
        self.org = make_org()

    def test_renders_a_pdf_to_pages(self):
        from vault.rendering import render_document_pages

        doc = _make_document(self.org, "cert.pdf", _pdf_bytes(pages=2))
        count = render_document_pages(doc, dpi=80)
        self.assertEqual(count, 2)
        self.assertEqual(DocumentPage.objects.filter(document=doc).count(), 2)
        page = DocumentPage.objects.filter(document=doc).first()
        self.assertTrue(page.image)
        self.assertIsNotNone(page.width)
        doc.refresh_from_db()
        self.assertEqual(doc.page_count, 2)

    def test_render_is_idempotent(self):
        from vault.rendering import render_document_pages

        doc = _make_document(self.org, "cert.pdf", _pdf_bytes(pages=1))
        render_document_pages(doc, dpi=80)
        render_document_pages(doc, dpi=80)
        self.assertEqual(DocumentPage.objects.filter(document=doc).count(), 1)

    def test_renders_an_image_as_one_page(self):
        from vault.rendering import render_document_pages

        doc = _make_document(self.org, "cert.png", _png_bytes())
        doc.mime_type = "image/png"
        doc.save(update_fields=["mime_type"])
        count = render_document_pages(doc, dpi=80)
        self.assertEqual(count, 1)

    def test_missing_poppler_degrades_to_zero(self):
        from vault import rendering

        doc = _make_document(self.org, "cert.pdf", _pdf_bytes())
        with mock.patch.object(
            rendering, "_rasterise_pdf", side_effect=FileNotFoundError("pdftoppm")
        ):
            count = rendering.render_document_pages(doc)
        self.assertEqual(count, 0)
        self.assertEqual(DocumentPage.objects.filter(document=doc).count(), 0)

    def test_unrenderable_type_is_skipped(self):
        from vault.rendering import render_document_pages

        doc = _make_document(self.org, "notes.txt", b"just text")
        doc.mime_type = "text/plain"
        doc.save(update_fields=["mime_type"])
        self.assertEqual(render_document_pages(doc), 0)


@override_settings(MEDIA_ROOT=_MEDIA)
class ProcessUploadedDocumentTests(TestCase):
    def setUp(self):
        self.org = make_org()

    def test_pipeline_hashes_renders_and_queues_extraction(self):
        from vault.tasks import process_uploaded_document

        doc = _make_document(self.org, "cert.pdf", _pdf_bytes(pages=1))
        self.assertEqual(doc.content_hash, "")

        with mock.patch("llm.tasks.extract_document_fields.delay") as queued:
            result = process_uploaded_document(doc.pk, self.org.pk)

        doc.refresh_from_db()
        self.assertEqual(len(doc.content_hash), 64)  # sha256 hex
        self.assertEqual(doc.page_count, 1)
        self.assertEqual(result["status"], "queued_extraction")
        queued.assert_called_once_with(doc.pk, self.org.pk)

    def test_pipeline_never_sets_verified_by_human(self):
        from vault.tasks import process_uploaded_document

        doc = _make_document(self.org, "cert.pdf", _pdf_bytes(pages=1))
        with mock.patch("llm.tasks.extract_document_fields.delay"):
            process_uploaded_document(doc.pk, self.org.pk)
        doc.refresh_from_db()
        self.assertFalse(doc.verified_by_human)

    def test_no_pages_does_not_queue_extraction(self):
        from vault import tasks

        doc = _make_document(self.org, "cert.pdf", _pdf_bytes(pages=1))
        with mock.patch("vault.rendering.render_document_pages", return_value=0), \
             mock.patch("llm.tasks.extract_document_fields.delay") as queued:
            result = tasks.process_uploaded_document(doc.pk, self.org.pk)
        self.assertEqual(result["status"], "no_pages_rendered")
        queued.assert_not_called()
