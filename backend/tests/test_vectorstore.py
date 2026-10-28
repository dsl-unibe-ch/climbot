import json
import unittest
from unittest.mock import patch

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.api.points_api import jsonable_encoder
from qdrant_client.models import PointsList, PointStruct, SparseVector

from app.config import Settings

with patch(
    "app.config.get_settings",
    return_value=Settings.model_construct(
        azure_tenant_id="test",
        azure_client_id="test",
        openai_api_key="test",
    ),
):
    from app.core import vectorstore


def request_size(points: list[PointStruct]) -> int:
    return len(jsonable_encoder(PointsList(points=points, shard_key=None)).encode("utf-8"))


def point(index: int, content: str = "test") -> PointStruct:
    return PointStruct(
        id=index,
        vector={
            "dense": [0.1, 0.2],
            "sparse": SparseVector(indices=[1, 3], values=[0.5, 0.7]),
        },
        payload={"content": content, "source": "test.pdf", "type": "text"},
    )


class UpsertPointsTests(unittest.TestCase):
    def setUp(self):
        self.client_patch = patch.object(vectorstore, "_sync")
        self.client = self.client_patch.start().return_value
        self.addCleanup(self.client_patch.stop)

    def assert_batches(self, points: list[PointStruct], limit: int) -> None:
        batches = [call.kwargs["points"] for call in self.client.upsert.call_args_list]
        self.assertEqual([p for batch in batches for p in batch], points)
        for batch, call in zip(batches, self.client.upsert.call_args_list, strict=True):
            self.assertTrue(batch)
            self.assertLessEqual(request_size(batch), limit)
            self.assertEqual(call.kwargs["collection_name"], "docs")
            self.assertIs(call.kwargs["wait"], True)

    def test_small_document_uses_one_request(self):
        points = [point(1), point(2)]
        vectorstore.upsert_points("docs", points)
        self.client.upsert.assert_called_once_with(collection_name="docs", points=points, wait=True)

    def test_empty_input_does_not_send_request(self):
        vectorstore.upsert_points("docs", [])
        self.client.upsert.assert_not_called()

    def test_large_document_stays_below_qdrant_limit(self):
        points = [point(i, "x" * 7_000_000) for i in range(6)]
        self.assertGreater(request_size(points), 33_554_432)
        vectorstore.upsert_points("docs", points)
        self.assertGreater(self.client.upsert.call_count, 1)
        self.assert_batches(points, 16 * 1024 * 1024)

    def test_utf8_payloads_and_sparse_vectors_are_counted(self):
        points = [point(i, "\u00e4\u6c14\U0001f30d" * 20) for i in range(5)]
        limit = request_size(points[:2])
        with patch.object(vectorstore, "_MAX_UPSERT_BYTES", limit):
            vectorstore.upsert_points("docs", points)
        self.assertEqual(self.client.upsert.call_count, 3)
        self.assert_batches(points, limit)

    def test_actual_http_bodies_stay_within_limit(self):
        points = [point(i, "\u00e4\u6c14\U0001f30d" * 20) for i in range(5)]
        limit = request_size(points[:2])
        bodies = []

        def respond(request: httpx.Request) -> httpx.Response:
            self.assertLessEqual(len(request.content), limit)
            self.assertEqual(request.url.params["wait"], "true")
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "result": {"operation_id": 1, "status": "completed"},
                    "status": "ok",
                    "time": 0.0,
                },
            )

        client = QdrantClient(url="http://qdrant.test", transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        with (
            patch.object(vectorstore, "_sync", return_value=client),
            patch.object(vectorstore, "_MAX_UPSERT_BYTES", limit),
        ):
            vectorstore.upsert_points("docs", points)

        self.assertEqual(len(bodies), 3)
        self.assertEqual(
            [p for body in bodies for p in body["points"]],
            [json.loads(p.model_dump_json()) for p in points],
        )

    def test_exact_limit_fits_one_request(self):
        points = [point(1), point(2)]
        with patch.object(vectorstore, "_MAX_UPSERT_BYTES", request_size(points)):
            vectorstore.upsert_points("docs", points)
        self.assertEqual(self.client.upsert.call_count, 1)
        self.assert_batches(points, request_size(points))

    def test_one_byte_over_limit_splits_request(self):
        points = [point(1), point(2)]
        limit = request_size(points) - 1
        with patch.object(vectorstore, "_MAX_UPSERT_BYTES", limit):
            vectorstore.upsert_points("docs", points)
        self.assertEqual(self.client.upsert.call_count, 2)
        self.assert_batches(points, limit)

    def test_single_oversized_point_raises_explicit_error(self):
        points = [point(1, "x" * 1000)]
        with (
            patch.object(vectorstore, "_MAX_UPSERT_BYTES", 512),
            self.assertRaisesRegex(ValueError, r"Point 1.*docs.*512"),
        ):
            vectorstore.upsert_points("docs", points)
        self.client.upsert.assert_not_called()

    def test_failed_batch_stops_upload_and_propagates(self):
        points = [point(i) for i in range(3)]
        self.client.upsert.side_effect = [None, RuntimeError("Qdrant unavailable")]
        with (
            patch.object(vectorstore, "_MAX_UPSERT_BYTES", request_size(points[:1])),
            self.assertRaisesRegex(RuntimeError, "Qdrant unavailable"),
        ):
            vectorstore.upsert_points("docs", points)
        self.assertEqual(self.client.upsert.call_count, 2)
