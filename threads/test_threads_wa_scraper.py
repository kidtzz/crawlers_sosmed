"""Unit test sederhana untuk ekstraksi berbagai link, pembersihan teks, deduplikasi, dan ekspor."""

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from threads.threads_wa_scraper import (
        BASE_DIR,
        DEFAULT_KEYWORDS,
        clean_post_text,
        deduplicate_and_sort_records,
        extract_relevant_links,
        extract_whatsapp_links,
        format_tanggal,
        load_environment,
        parse_keywords,
        posts_to_records,
        prioritize_comment_candidates,
        resolve_project_path,
        save_to_csv,
        save_to_markdown,
    )
except ImportError:
    from threads_wa_scraper import (
        BASE_DIR,
        DEFAULT_KEYWORDS,
        clean_post_text,
        deduplicate_and_sort_records,
        extract_relevant_links,
        extract_whatsapp_links,
        format_tanggal,
        load_environment,
        parse_keywords,
        posts_to_records,
        prioritize_comment_candidates,
        resolve_project_path,
        save_to_csv,
        save_to_markdown,
    )


class TestThreadsWAScraper(unittest.TestCase):
    def test_extract_whatsapp_and_drive_and_other_links(self) -> None:
        text = (
            "Gabung WA: https://chat.whatsapp.com/AbCdEfGhIjKlMnOpQrStUv. "
            "Materi: https://drive.google.com/drive/folders/1AbCdEfGh?usp=sharing&fbclid=123"
        )
        hrefs = [
            "https://l.threads.com/?u=https%3A%2F%2Fdocs.google.com%2Fforms%2Fd%2Fe%2F1FAIpQL%2Fviewform&e=1",
            "https://www.threads.com/@someone/post/123",
            "https://t.me/ielts_community_id",
        ]
        links = extract_relevant_links(text, hrefs)
        self.assertEqual(
            links,
            [
                ("Grup WhatsApp", "https://chat.whatsapp.com/AbCdEfGhIjKlMnOpQrStUv"),
                ("Google Drive", "https://drive.google.com/drive/folders/1AbCdEfGh?usp=sharing"),
                ("Google Docs/Form", "https://docs.google.com/forms/d/e/1FAIpQL/viewform"),
                ("Telegram", "https://t.me/ielts_community_id"),
            ],
        )
        self.assertEqual(
            extract_whatsapp_links(text, hrefs),
            ["https://chat.whatsapp.com/AbCdEfGhIjKlMnOpQrStUv"],
        )

    def test_clean_post_text_and_format_tanggal(self) -> None:
        raw = "@user_ielts\n20h\nFollow\nYuk gabung grup IELTS gratis!\nTranslate\n14\n3"
        self.assertEqual(clean_post_text(raw, username="@user_ielts"), "Yuk gabung grup IELTS gratis!")
        formatted = format_tanggal("2026-11-25T10:00:00Z")
        self.assertRegex(formatted, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_deduplicate_sort_and_save_csv_and_md(self) -> None:
        records = [
            {
                "kategori": "Google Drive",
                "link": "https://drive.google.com/drive/folders/123",
                "username": "@drive_user",
                "tanggal": "2026-11-25 12:00",
                "sumber": "Postingan",
                "keyword": "IELTS",
                "ringkasan_info": "Bank soal IELTS",
                "post_url": "https://www.threads.com/@drive_user/post/1",
            },
            {
                "kategori": "Grup WhatsApp",
                "link": "https://chat.whatsapp.com/AbCdEfGhIjKlMnOpQrStUv",
                "username": "@wa_user1",
                "tanggal": "2026-11-25 10:00",
                "sumber": "Postingan",
                "keyword": "grup WA IELTS",
                "ringkasan_info": "Grup belajar IELTS",
                "post_url": "https://www.threads.com/@wa_user1/post/2",
            },
            {
                "kategori": "Grup WhatsApp",
                "link": "https://chat.whatsapp.com/AbCdEfGhIjKlMnOpQrStUv",
                "username": "@wa_user2",
                "tanggal": "2026-11-25 11:00",
                "sumber": "Komentar",
                "keyword": "komunitas IELTS",
                "ringkasan_info": "Duplikat grup",
                "post_url": "https://www.threads.com/@wa_user2/post/3",
            },
        ]
        unique = deduplicate_and_sort_records(records)
        self.assertEqual(len(unique), 2)
        self.assertEqual(unique[0]["kategori"], "Grup WhatsApp")
        self.assertEqual(unique[0]["username"], "@wa_user1")
        self.assertEqual(unique[1]["kategori"], "Google Drive")

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "out.csv"
            md_file = Path(tmpdir) / "out.md"
            save_to_csv(unique, csv_file)
            save_to_markdown(unique, md_file)

            with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["no"], "1")
            self.assertEqual(rows[0]["kategori"], "Grup WhatsApp")
            self.assertIn("## Grup WhatsApp (1)", md_file.read_text(encoding="utf-8"))

    def test_parse_keywords_and_comment_priority(self) -> None:
        self.assertEqual(parse_keywords(None), DEFAULT_KEYWORDS)
        self.assertEqual(
            parse_keywords(" grup IELTS , belajar english , grup IELTS "),
            ["grup IELTS", "belajar english"],
        )
        ranked = prioritize_comment_candidates(
            [
                ("https://threads.com/@a/post/1", "k", "Cuaca hari ini cerah"),
                ("https://threads.com/@b/post/2", "k", "Yang mau link grup WA IELTS komen ya"),
            ]
        )
        self.assertEqual(ranked[0][0], "https://threads.com/@b/post/2")

    def test_posts_to_records_marks_post_vs_comment(self) -> None:
        raw_items = [
            {
                "username": "@author",
                "tanggal": "2026-11-25T10:00:00Z",
                "teks": "Yang mau belajar IELTS cek komen ya",
                "hrefs": [],
                "post_url": "https://www.threads.com/@author/post/ABC?x=1",
            },
            {
                "username": "@commenter",
                "tanggal": "2026-11-25T10:05:00Z",
                "teks": "Ini linknya https://chat.whatsapp.com/IeltsGroup1234567890",
                "hrefs": [],
                "post_url": "https://www.threads.com/@commenter/post/DEF",
            },
        ]
        records = posts_to_records(
            raw_items,
            keyword="grup WA IELTS",
            parent_post_url="https://www.threads.com/@author/post/ABC",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["sumber"], "Komentar")
        self.assertEqual(records[0]["username"], "@commenter")


    def test_load_environment_separates_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_file = tmp / ".env"
            cred_file = tmp / ".env.credentials"
            env_file.write_text("THREADS_KEYWORDS=ielts,english\nHEADLESS=true\n", encoding="utf-8")
            cred_file.write_text("THREADS_USERNAME=akun_test\nTHREADS_PASSWORD=rahasia\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                loaded_cred = load_environment(env_path=env_file, credentials_path=cred_file)
                self.assertEqual(loaded_cred, cred_file)
                self.assertEqual(os.environ["THREADS_USERNAME"], "akun_test")
                self.assertEqual(os.environ["THREADS_PASSWORD"], "rahasia")
                self.assertEqual(os.environ["THREADS_KEYWORDS"], "ielts,english")
                self.assertEqual(os.environ["HEADLESS"], "true")

        self.assertEqual(resolve_project_path("hasil.csv"), BASE_DIR / "hasil.csv")


if __name__ == "__main__":
    unittest.main()
