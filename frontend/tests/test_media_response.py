import unittest

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient
from streamlit.web.server.starlette.starlette_gzip_middleware import SelectiveGZipMiddleware


class MediaHttpTests(unittest.TestCase):
    def test_browser_can_fetch_media_response(self):
        async def media(request):
            return Response(
                b"image content", media_type="image/png", headers={"Content-Encoding": "identity"}
            )

        app = Starlette(
            routes=[Route("/media/test.png", media)],
            middleware=[Middleware(SelectiveGZipMiddleware)],
        )
        with TestClient(app) as client:
            for encoding in ("gzip", "identity"):
                with self.subTest(encoding=encoding):
                    response = client.get("/media/test.png", headers={"Accept-Encoding": encoding})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["content-type"], "image/png")
                    self.assertEqual(response.content, b"image content")


class MediaResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_headers_precede_body(self):
        for encoding in (b"identity", b"gzip"):
            for content_type in (b"image/png", b"image/jpeg", b"audio/wav", b"video/mp4"):
                with self.subTest(encoding=encoding, content_type=content_type):
                    messages = []

                    async def app(scope, receive, send):
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 200,
                                "headers": [
                                    (b"content-type", content_type),
                                    (b"content-encoding", b"identity"),
                                ],
                            }
                        )
                        await send({"type": "http.response.body", "body": b"media content"})

                    async def receive():
                        return {"type": "http.request"}

                    async def send(message):
                        messages.append(message)

                    await SelectiveGZipMiddleware(app)(
                        {
                            "type": "http",
                            "path": "/media/test.png",
                            "headers": [(b"accept-encoding", encoding)],
                        },
                        receive,
                        send,
                    )
                    self.assertEqual(
                        [message["type"] for message in messages],
                        ["http.response.start", "http.response.body"],
                    )
                    self.assertEqual(messages[-1]["body"], b"media content")


if __name__ == "__main__":
    unittest.main()
