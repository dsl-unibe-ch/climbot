import base64
import hashlib
import random
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from openai.types.chat import ChatCompletion
from PIL import Image
from qdrant_client.models import PointsList, PointStruct

from app.config import Settings

with patch(
    "app.config.get_settings",
    return_value=Settings.model_construct(
        azure_tenant_id="test", azure_client_id="test", openai_api_key="test"
    ),
):
    from app.core import embeddings, ingestion


def image_bytes(
    mode="RGB", size=(128, 128), color: str | tuple[int, int, int, int] = "white", **kwargs
) -> bytes:
    output = BytesIO()
    Image.new(mode, size, color).save(output, format="PNG", **kwargs)
    return output.getvalue()


class ImagePreparationTests(unittest.TestCase):
    def test_small_image_is_unchanged(self):
        original = image_bytes()
        self.assertEqual(ingestion._prepare_image(original, "png"), (original, "png"))

    def test_large_image_fits_real_binary_and_request_limits(self):
        image = Image.frombytes("RGB", (2400, 1800), random.Random(42).randbytes(2400 * 1800 * 3))
        output = BytesIO()
        image.save(output, format="PNG")
        original = output.getvalue()
        self.assertGreater(len(original), ingestion._MAX_IMAGE_BYTES)

        compressed, ext = ingestion._prepare_image(original, "png")
        self.assertEqual(ext, "jpg")
        self.assertLessEqual(len(compressed), ingestion._MAX_IMAGE_BYTES)
        with Image.open(BytesIO(compressed)) as result:
            self.assertEqual(result.format, "JPEG")
            self.assertEqual(result.size, image.size)
        point = PointStruct(
            id=1,
            vector={"dense": [0.1] * 1024},
            payload={"image_base64": base64.b64encode(compressed).decode()},
        )
        self.assertLess(
            len(PointsList(points=[point]).model_dump_json().encode()), 16 * 1024 * 1024
        )

    def test_transparency_is_flattened_on_white(self):
        original = image_bytes(mode="RGBA", color=(255, 0, 0, 0), compress_level=0)
        with patch.object(ingestion, "_MAX_IMAGE_BYTES", 4096):
            compressed, _ = ingestion._prepare_image(original, "png")
        with Image.open(BytesIO(compressed)) as result:
            self.assertEqual(result.mode, "RGB")
            self.assertEqual(result.getpixel((64, 64)), (255, 255, 255))

    def test_resizing_preserves_aspect_ratio_when_quality_alone_is_not_enough(self):
        image = Image.frombytes("RGB", (800, 400), random.Random(1).randbytes(800 * 400 * 3))
        output = BytesIO()
        image.save(output, format="PNG")
        with patch.object(ingestion, "_MAX_IMAGE_BYTES", 40_000):
            compressed, _ = ingestion._prepare_image(output.getvalue(), "png")
        self.assertLessEqual(len(compressed), 40_000)
        with Image.open(BytesIO(compressed)) as result:
            self.assertLess(result.width, 800)
            self.assertAlmostEqual(result.width / result.height, 2, delta=0.02)

    def test_invalid_oversized_image_raises(self):
        with (
            patch.object(ingestion, "_MAX_IMAGE_BYTES", 4),
            self.assertRaises(OSError),
        ):
            ingestion._prepare_image(b"not an image", "png")


class ImagePipelineTests(unittest.TestCase):
    def setUp(self):
        self.original = image_bytes(compress_level=0)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(ingestion, "_MAX_IMAGE_BYTES", 4096))
        self.embed_image = stack.enter_context(
            patch.object(ingestion, "embed_image", return_value=([0.1, 0.2], "chart"))
        )
        stack.enter_context(patch.object(ingestion, "embed_sparse", return_value=[([1], [0.5])]))
        self.upsert_points = stack.enter_context(patch.object(ingestion, "upsert_points"))

    def assert_compressed_point(self):
        point = self.upsert_points.call_args.args[1][0]
        payload = point.payload
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["image_ext"], "jpg")
        stored = base64.b64decode(payload["image_base64"])
        self.assertLessEqual(len(stored), 4096)
        self.embed_image.assert_called_once_with(stored)
        with Image.open(BytesIO(stored)) as image:
            self.assertEqual(image.format, "JPEG")
        return point

    def test_standalone_image_compresses_but_keeps_original_hash_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "chart.png"
            path.write_bytes(self.original)
            self.assertEqual(ingestion._ingest_standalone_image(path, root), 1)
            self.assertEqual(path.read_bytes(), self.original)
        point = self.assert_compressed_point()
        digest = hashlib.sha1(self.original).hexdigest()[:16]  # noqa: S324
        self.assertEqual(point.id, str(uuid.uuid5(ingestion._ID_NS, f"chart.png:{digest}")))

    def test_pdf_image_is_compressed_and_indexed_not_skipped(self):
        page = MagicMock()
        page.get_images.return_value = [(7,)]
        pix = MagicMock(width=128, height=128, n=3, alpha=0)
        pix.tobytes.return_value = self.original
        with (
            patch.object(ingestion.fitz, "open", return_value=[page]),
            patch.object(ingestion.fitz, "Pixmap", return_value=pix),
        ):
            self.assertEqual(
                ingestion._ingest_pdf_images(Path("/docs/chart.pdf"), Path("/docs")), 1
            )
        point = self.assert_compressed_point()
        self.assertEqual(point.id, str(uuid.uuid5(ingestion._ID_NS, "chart.pdf:img:0:7")))


class VisionMimeTests(unittest.TestCase):
    def test_vision_request_uses_actual_image_format(self):
        for format_name, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg")):
            with self.subTest(format=format_name):
                output = BytesIO()
                Image.new("RGB", (10, 10)).save(output, format=format_name)
                client = MagicMock()
                client.chat.completions.create.return_value.choices[0].message.content = "chart"
                client.chat.completions.create.return_value.choices[0].message.refusal = None
                client.chat.completions.create.return_value.choices[0].finish_reason = "stop"
                with (
                    patch.object(embeddings, "_openai", return_value=client),
                    patch.object(embeddings, "embed_texts", return_value=[[0.1]]),
                ):
                    self.assertEqual(embeddings.embed_image(output.getvalue()), ([0.1], "chart"))
                messages = client.chat.completions.create.call_args.kwargs["messages"]
                self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], 0)
                self.assertTrue(
                    messages[0]["content"][1]["image_url"]["url"].startswith(f"data:{mime};base64,")
                )


class VisionValidationTests(unittest.TestCase):
    def completion(self, content="chart", refusal=None, finish_reason="stop", empty_choices=False):
        return ChatCompletion.model_validate(
            {
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-vision",
                "choices": []
                if empty_choices
                else [
                    {
                        "index": 0,
                        "finish_reason": finish_reason,
                        "message": {"role": "assistant", "content": content, "refusal": refusal},
                    }
                ],
            }
        )

    def test_invalid_descriptions_never_reach_embedding(self):
        cases = [
            {"empty_choices": True},
            {"content": None},
            {"content": " \n "},
            {"refusal": "Image request declined"},
            {"finish_reason": "content_filter"},
            {"finish_reason": "length"},
            {"finish_reason": "tool_calls"},
            {"content": "I'm unable to help with the content of this image."},
            {"content": "I\u2019m sorry, I cannot describe this image."},
            {"content": "Sorry, I can't assist with that request."},
            {"content": "I am unable to analyze this image."},
        ]
        for case in cases:
            with self.subTest(case=case):
                client = MagicMock()
                client.chat.completions.create.return_value = self.completion(**case)
                with (
                    patch.object(embeddings, "_openai", return_value=client),
                    patch.object(embeddings, "embed_texts") as embed,
                    self.assertRaisesRegex(ValueError, "Vision model"),
                ):
                    embeddings.embed_image(image_bytes())
                embed.assert_not_called()

    def test_valid_description_is_trimmed_and_embedded(self):
        for description in (
            "The image shows the Guix HPC logo; there is no climate data.",
            'The chart text says "I cannot help".',
        ):
            with self.subTest(description=description):
                client = MagicMock()
                client.chat.completions.create.return_value = self.completion(
                    content=f" \n{description}\n "
                )
                with (
                    patch.object(embeddings, "_openai", return_value=client),
                    patch.object(embeddings, "embed_texts", return_value=[[0.1]]) as embed,
                ):
                    self.assertEqual(embeddings.embed_image(image_bytes()), ([0.1], description))
                embed.assert_called_once_with([description])
