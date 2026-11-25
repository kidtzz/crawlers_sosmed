"""Unit test untuk ekstraksi link, filter kualitas (spam, topik, duplikat, validasi WA), dan ekspor."""

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from crawler_utils import (  # noqa: E402
    apply_quality_filters_from_env,
    derive_topic_keywords,
    filter_and_validate_records,
    get_spam_keywords,
    is_relevant_topic,
    is_spam_text,
    parse_whatsapp_invite_html,
)
from threads.scraper import (  # noqa: E402
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

    def test_deduplicate_sort_newest_and_save_csv_and_md(self) -> None:
        records = [
            {
                "kategori": "Google Drive",
                "nama_grup": "-",
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
                "nama_grup": "IELTS Study",
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
                "nama_grup": "IELTS Study",
                "link": "https://chat.whatsapp.com/AbCdEfGhIjKlMnOpQrStUv",
                "username": "@wa_user2",
                "tanggal": "2026-11-25 11:00",
                "sumber": "Komentar",
                "keyword": "komunitas IELTS",
                "ringkasan_info": "Repost grup belajar IELTS terbaru",
                "post_url": "https://www.threads.com/@wa_user2/post/3",
            },
        ]
        unique = deduplicate_and_sort_records(records)
        self.assertEqual(len(unique), 2)
        # Urutan terbaru di atas (12:00 lalu 11:00) dan duplikat WA mempertahankan yang lebih baru
        self.assertEqual(unique[0]["tanggal"], "2026-11-25 12:00")
        self.assertEqual(unique[1]["username"], "@wa_user2")

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "out.csv"
            md_file = Path(tmpdir) / "out.md"
            save_to_csv(unique, csv_file)
            save_to_markdown(unique, md_file)

            with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["no"], "1")
            self.assertEqual(rows[1]["nama_grup"], "IELTS Study")
            self.assertIn("| No | Tanggal | Kategori | Nama Grup |", md_file.read_text(encoding="utf-8"))

    def test_parse_whatsapp_invite_html(self) -> None:
        valid_html = (
            '<html><head><title>WhatsApp Group Invite</title>'
            '<meta property="og:title" content="IELTS Speaking &amp; Writing Club" /></head></html>'
        )
        self.assertEqual(parse_whatsapp_invite_html(valid_html), (True, "IELTS Speaking & Writing Club"))

        dead_html = (
            '<html><head><title>WhatsApp Group Invite</title>'
            '<meta property="og:title" content="" /></head>'
            '<body><h3></h3><h3>Looks like you don\'t have WhatsApp installed!</h3></body></html>'
        )
        self.assertEqual(parse_whatsapp_invite_html(dead_html), (False, ""))

    def test_spam_and_topic_filters_and_validation_pipeline(self) -> None:
        spam_kws = get_spam_keywords("promo vip")
        self.assertTrue(is_spam_text("Daftar slot gacor maxwin hari ini", spam_kws))
        self.assertTrue(is_spam_text("Jual akun murah meriah", spam_kws))
        self.assertFalse(is_spam_text("Good IELTS book and keyword tips", spam_kws))

        topics = derive_topic_keywords(None, ["grup WA IELTS", "komunitas belajar bahasa Inggris"])
        self.assertIn("ielts", topics)
        self.assertIn("inggris", topics)
        self.assertTrue(is_relevant_topic("Yuk latihan IELTS bareng", topics))
        self.assertFalse(is_relevant_topic("Grup mabar game online santai", topics))

        records = [
            {
                "kategori": "Grup WhatsApp",
                "nama_grup": "-",
                "link": "https://chat.whatsapp.com/ValidIelts1234567890",
                "username": "@ielts_id",
                "tanggal": "2026-11-25 09:00",
                "sumber": "Postingan",
                "keyword": "grup WA IELTS",
                "ringkasan_info": "Yuk gabung belajar bareng",
                "post_url": "https://threads.com/@ielts_id/post/1",
            },
            {
                "kategori": "Grup WhatsApp",
                "nama_grup": "-",
                "link": "https://chat.whatsapp.com/DeadGroup1234567890",
                "username": "@dead_user",
                "tanggal": "2026-11-25 13:00",
                "sumber": "Postingan",
                "keyword": "grup WA IELTS",
                "ringkasan_info": "Grup IELTS lama",
                "post_url": "https://threads.com/@dead_user/post/2",
            },
            {
                "kategori": "Grup WhatsApp",
                "nama_grup": "-",
                "link": "https://chat.whatsapp.com/SpamGroup1234567890",
                "username": "@spam_user",
                "tanggal": "2026-11-25 14:00",
                "sumber": "Postingan",
                "keyword": "grup WA IELTS",
                "ringkasan_info": "Link grup slot gacor IELTS",
                "post_url": "https://threads.com/@spam_user/post/3",
            },
            {
                "kategori": "Grup WhatsApp",
                "nama_grup": "-",
                "link": "https://chat.whatsapp.com/OffTopic1234567890",
                "username": "@mabar",
                "tanggal": "2026-11-25 12:00",
                "sumber": "Postingan",
                "keyword": "chat.whatsapp.com",
                "ringkasan_info": "Grup mabar mobile legends",
                "post_url": "https://threads.com/@mabar/post/4",
            },
            {
                "kategori": "Google Drive",
                "nama_grup": "-",
                "link": "https://drive.google.com/drive/folders/IELTSFolder",
                "username": "@mentor",
                "tanggal": "2026-11-25 11:00",
                "sumber": "Postingan",
                "keyword": "grup WA IELTS",
                "ringkasan_info": "Modul latihan IELTS gratis",
                "post_url": "https://threads.com/@mentor/post/5",
            },
        ]

        def fake_wa_checker(url: str) -> tuple[bool, str, str]:
            mapping = {
                "https://chat.whatsapp.com/ValidIelts1234567890": (True, "IELTS Speaking Community", "valid"),
                "https://chat.whatsapp.com/DeadGroup1234567890": (False, "", "mati_atau_direset"),
                "https://chat.whatsapp.com/OffTopic1234567890": (True, "Mabar MLBB Squad", "valid"),
            }
            return mapping.get(url, (False, "", "unknown"))

        filtered = filter_and_validate_records(
            records,
            topic_keywords=["ielts", "english", "inggris"],
            spam_keywords=spam_kws,
            validate_wa=True,
            wa_delay_min=0.0,
            wa_delay_max=0.0,
            only_whatsapp=False,
            wa_checker=fake_wa_checker,
        )

        # Hanya Google Drive (11:00) dan Valid WA Group (09:00) yang lolos, terurut dari terbaru
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0]["link"], "https://drive.google.com/drive/folders/IELTSFolder")
        self.assertEqual(filtered[1]["link"], "https://chat.whatsapp.com/ValidIelts1234567890")
        self.assertEqual(filtered[1]["nama_grup"], "IELTS Speaking Community")

        with patch.dict(os.environ, {"ONLY_WHATSAPP_LINKS": "true", "TOPIC_KEYWORDS": "ielts"}, clear=False):
            wa_only = apply_quality_filters_from_env(records, ["grup WA IELTS"], wa_checker=fake_wa_checker)
            self.assertEqual(len(wa_only), 1)
            self.assertEqual(wa_only[0]["nama_grup"], "IELTS Speaking Community")

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
