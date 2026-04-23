import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from api.routers import document as document_router


class DocumentEmbeddingsTest(unittest.TestCase):
    def test_get_embeddings_batches_requests_and_sleeps_between_batches(self) -> None:
        batch_responses = [
            SimpleNamespace(
                embeddings=[
                    SimpleNamespace(values=[1.0, 0.0]),
                    SimpleNamespace(values=[2.0, 0.0]),
                ]
            ),
            SimpleNamespace(
                embeddings=[
                    SimpleNamespace(values=[3.0, 0.0]),
                ]
            ),
        ]

        embed_content = MagicMock(side_effect=batch_responses)
        fake_client = SimpleNamespace(
            models=SimpleNamespace(embed_content=embed_content)
        )

        with (
            patch.object(document_router.os, "getenv", return_value="test-key"),
            patch.object(document_router.genai, "Client", return_value=fake_client),
            patch.object(document_router.time, "sleep") as mock_sleep,
        ):
            embeddings = document_router.get_embeddings(
                ["one", "two", "three"],
                batch_size=2,
                batch_sleep_seconds=0.25,
            )

        self.assertEqual(embeddings, [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
        self.assertEqual(embed_content.call_count, 2)
        self.assertEqual(
            embed_content.call_args_list[0].kwargs["contents"], ["one", "two"]
        )
        self.assertEqual(embed_content.call_args_list[1].kwargs["contents"], ["three"])
        mock_sleep.assert_called_once_with(0.25)

    def test_get_embeddings_retries_resource_exhausted_batch(self) -> None:
        class FakeClientError(Exception):
            def __init__(self, status_code: int):
                super().__init__("rate limited")
                self.status_code = status_code

        embed_content = MagicMock(
            side_effect=[
                FakeClientError(429),
                SimpleNamespace(
                    embeddings=[
                        SimpleNamespace(values=[9.0, 1.0]),
                    ]
                ),
            ]
        )
        fake_client = SimpleNamespace(
            models=SimpleNamespace(embed_content=embed_content)
        )

        with (
            patch.object(document_router.os, "getenv", return_value="test-key"),
            patch.object(document_router.genai, "Client", return_value=fake_client),
            patch.object(document_router.time, "sleep") as mock_sleep,
        ):
            embeddings = document_router.get_embeddings(
                ["only-one"],
                batch_size=1,
                batch_sleep_seconds=0.25,
                retry_sleep_seconds=1.5,
                max_retries=2,
            )

        self.assertEqual(embeddings, [[9.0, 1.0]])
        self.assertEqual(embed_content.call_count, 2)
        mock_sleep.assert_called_once_with(1.5)


if __name__ == "__main__":
    unittest.main()
