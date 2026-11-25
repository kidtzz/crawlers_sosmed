"""Unit test untuk scraper Twitter (X) dan ekstraksi link tanpa skema / t.co."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from shared.crawler_utils import extract_relevant_links, posts_to_records
from twitter.scraper import (
    BASE_DIR,
    build_search_url,
    enrich_tweet_links,
    has_session_cookies,
    load_environment,
    resolve_project_path,
    should_resolve_tco,
)


class TestXWAScraper(unittest.TestCase):
    def test_build_search_url_modes(self) -> None:
        self.assertEqual(
            build_search_url("grup WA IELTS", "live"),
            "https://x.com/search?q=grup+WA+IELTS&src=typed_query&f=live",
        )
        self.assertEqual(
            build_search_url("grup WA IELTS", "top"),
            "https://x.com/search?q=grup+WA+IELTS&src=typed_query",
        )

    def test_schemeless_links_and_tco_enrichment(self) -> None:
        tweet = {
            "username": "@ielts_buddy",
            "tanggal": "2026-11-25T10:00:00Z",
            "teks": "Join WA chat.whatsapp.com/IeltsGroup1234567890 & materi drive.google.com/drive/folders/1AbC…",
            "anchors": [
                {"href": "https://t.co/xyz123", "text": "drive.google.com/drive/folders/1AbC…"}
            ],
            "hrefs": ["https://t.co/xyz123"],
            "post_url": "https://x.com/ielts_buddy/status/999",
        }

        self.assertTrue(should_resolve_tco("drive.google.com/drive/folders/1AbC…", tweet["teks"]))
        self.assertFalse(should_resolve_tco("nytimes.com/article…", "Berita hari ini"))

        with patch(
            f"{enrich_tweet_links.__module__}.resolve_tco_url",
            return_value="https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp",
        ):
            enrich_tweet_links([tweet], resolve_tco=True)

        links = extract_relevant_links(tweet["teks"], tweet["hrefs"])
        self.assertEqual(
            links,
            [
                ("Grup WhatsApp", "https://chat.whatsapp.com/IeltsGroup1234567890"),
                ("Google Drive", "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp"),
            ],
        )

        records = posts_to_records([tweet], keyword="grup WA IELTS")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["sumber"], "Postingan")

    def test_has_session_cookies(self) -> None:
        ctx_ok = MagicMock()
        ctx_ok.cookies.return_value = [
            {"name": "auth_token", "value": "abc"},
            {"name": "ct0", "value": "def"},
        ]
        self.assertTrue(has_session_cookies(ctx_ok))

        ctx_missing = MagicMock()
        ctx_missing.cookies.return_value = [{"name": "guest_id", "value": "123"}]
        self.assertFalse(has_session_cookies(ctx_missing))

    def test_load_environment_and_path_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_file = tmp / ".env"
            cred_file = tmp / ".env.credentials"
            env_file.write_text("X_KEYWORDS=ielts,toefl\nX_SEARCH_MODE=top\n", encoding="utf-8")
            cred_file.write_text("X_USERNAME=cadangan_x\nX_PASSWORD=rahasia_x\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                loaded = load_environment(env_path=env_file, credentials_path=cred_file)
                self.assertEqual(loaded, cred_file)
                self.assertEqual(os.environ["X_USERNAME"], "cadangan_x")
                self.assertEqual(os.environ["X_SEARCH_MODE"], "top")

        self.assertEqual(resolve_project_path("hasil.csv"), BASE_DIR / "hasil.csv")


if __name__ == "__main__":
    unittest.main()
