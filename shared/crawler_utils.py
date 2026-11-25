"""Utilitas bersama untuk crawler Playwright (ekstraksi link, filter kualitas, validasi WA, & ekspor)."""

import csv
import html
import os
import random
import re
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

try:
    from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
except ImportError:  # Memungkinkan impor fungsi helper untuk pengujian tanpa Playwright
    BrowserContext = Any  # type: ignore[assignment, misc]
    Page = Any  # type: ignore[assignment, misc]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment, misc]


# =========================
# KONSTANTA BERSAMA & FILTER
# =========================

DEFAULT_KEYWORDS = [
    "grup WA IELTS",
    "grup belajar IELTS",
    "komunitas IELTS",
    "IELTS WhatsApp group",
    "chat.whatsapp.com IELTS",
    "grup WA bahasa Inggris",
    "komunitas belajar bahasa Inggris",
    "chat.whatsapp.com english",
]

# Daftar kata spam default (judi online, jualan/promo, penipuan, konten dewasa)
DEFAULT_SPAM_KEYWORDS = [
    "judi",
    "slot",
    "gacor",
    "maxwin",
    "zeus",
    "togel",
    "sbobet",
    "casino",
    "betting",
    "jackpot",
    "depo",
    "withdraw",
    "wd",
    "scatter",
    "jp",
    "pragmatic",
    "jual",
    "jual akun",
    "open bo",
    "vcs",
    "bokep",
    "pinjol",
    "dana kaget",
    "cuan instan",
    "binary",
    "jasa joki",
    "joki tugas",
    "joki skripsi",
    "open order",
    "ready stock",
    "murah meriah",
    "diskon",
    "dropship",
    "reseller",
]

# Kata umum non-topik yang diabaikan saat mengekstrak kata topik otomatis dari keyword pencarian
GENERIC_SEARCH_WORDS = {
    "grup",
    "group",
    "wa",
    "whatsapp",
    "chat",
    "com",
    "komunitas",
    "community",
    "link",
    "join",
    "gabung",
    "chat.whatsapp.com",
}

# Placeholder WhatsApp ketika invite link mati/direset/tidak ditemukan
INVALID_WA_TITLES = {
    "",
    "whatsapp",
    "whatsapp group invite",
    "group invite",
    "invite link has expired",
    "this invite link was reset",
    "couldn't find this group",
}

WA_LINK_REGEX = re.compile(
    r"(?:https?://)?chat\.whatsapp\.com/(?:invite/)?([A-Za-z0-9_-]{10,})",
    re.IGNORECASE,
)
GENERAL_URL_REGEX = re.compile(r"https?://[^\s<>\"'()…]+", re.IGNORECASE)
SCHEMELESS_URL_REGEX = re.compile(
    r"(?<![A-Za-z0-9@/_.-])(?:"
    r"drive\.google\.com|docs\.google\.com|forms\.gle|wa\.me|t\.me|telegram\.me|"
    r"discord\.gg|discord\.com/invite|linktr\.ee|bit\.ly|s\.id|"
    r"notion\.site|notion\.so|dropbox\.com|onedrive\.live\.com|1drv\.ms|canva\.com"
    r")/[^\s<>\"'()…]+",
    re.IGNORECASE,
)

IGNORED_DOMAINS = {
    "threads.com",
    "www.threads.com",
    "threads.net",
    "www.threads.net",
    "l.threads.com",
    "l.threads.net",
    "instagram.com",
    "www.instagram.com",
    "l.instagram.com",
    "help.instagram.com",
    "privacycenter.instagram.com",
    "facebook.com",
    "www.facebook.com",
    "l.facebook.com",
    "fb.com",
    "meta.com",
    "about.meta.com",
    "whatsapp.com",
    "www.whatsapp.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "t.co",
    "pic.twitter.com",
    "pic.x.com",
}

TRACKING_QUERY_PARAMS = {
    "fbclid",
    "igshid",
    "igsh",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "s",
    "t",
}

COMMENT_PRIORITY_HINTS = (
    "komen",
    "comment",
    "reply",
    "balas",
    "link",
    "grup",
    "group",
    "wa",
    "whatsapp",
    "drive",
    "materi",
    "gabung",
    "join",
    "dm",
    "ielts",
    "english",
)

DEFAULT_SCROLL_DELAY_MIN = 1.2
DEFAULT_SCROLL_DELAY_MAX = 2.5
DEFAULT_MAX_SCROLLS_PER_SESSION = 30
DEFAULT_MAX_SCROLLS_PER_KEYWORD = 4
DEFAULT_MAX_COMMENT_POSTS = 10
DEFAULT_MAX_COMMENT_SCROLLS = 2
DEFAULT_WA_CHECK_DELAY_MIN = 2.0
DEFAULT_WA_CHECK_DELAY_MAX = 4.5
DEFAULT_MAX_WA_CHECKS = 30


# =========================
# KONFIGURASI & ENV HELPER
# =========================

def parse_int_env(name: str, default: int, minimum: int = 0) -> int:
    """Membaca konfigurasi integer dari environment variable dengan batas minimum."""
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def parse_float_env(name: str, default: float, minimum: float = 0.2) -> float:
    """Membaca konfigurasi float dari environment variable dengan batas minimum."""
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def parse_csv_list(raw_value: str | None) -> list[str]:
    """Mengurai nilai dipisahkan koma menjadi list unik tanpa spasi berlebih."""
    if not raw_value or not raw_value.strip():
        return []
    parsed = [item.strip() for item in raw_value.split(",") if item.strip()]
    return list(dict.fromkeys(parsed))


def parse_keywords(raw_keywords: str | None, default_keywords: list[str] | None = None) -> list[str]:
    """Mengurai daftar keyword dipisahkan koma dari .env, atau memakai default."""
    fallback = list(default_keywords or DEFAULT_KEYWORDS)
    parsed = parse_csv_list(raw_keywords)
    return parsed if parsed else fallback


def derive_topic_keywords(raw_topic_keywords: str | None, search_keywords: list[str]) -> list[str]:
    """Mengambil kata kunci topik dari TOPIC_KEYWORDS, atau menurunkannya dari keyword pencarian."""
    explicit = [kw.lower() for kw in parse_csv_list(raw_topic_keywords)]
    if explicit:
        return explicit

    derived: list[str] = []
    for kw in search_keywords:
        for token in re.findall(r"[A-Za-z0-9]+", kw.lower()):
            if len(token) >= 3 and token not in GENERIC_SEARCH_WORDS and token not in derived:
                derived.append(token)
    return derived


def get_spam_keywords(raw_spam_keywords: str | None) -> list[str]:
    """Menggabungkan daftar kata spam bawaan dengan tambahan kata spam dari .env."""
    custom = [kw.lower() for kw in parse_csv_list(raw_spam_keywords)]
    combined = list(DEFAULT_SPAM_KEYWORDS)
    for kw in custom:
        if kw not in combined:
            combined.append(kw)
    return combined


def resolve_project_path(raw_path: str, base_dir: Path) -> Path:
    """Mengubah path relatif menjadi path di dalam folder crawler terkait."""
    path = Path(raw_path)
    return path if path.is_absolute() else base_dir / path


def load_environment(
    base_dir: Path,
    env_path: Path | None = None,
    credentials_path: Path | None = None,
) -> Path:
    """Memuat konfigurasi dari .env lalu memuat kredensial terpisah dari .env.credentials."""
    target_env = env_path or (base_dir / ".env")
    if target_env.exists():
        load_dotenv(dotenv_path=target_env)

    cred_file = credentials_path or resolve_project_path(
        os.getenv("CREDENTIALS_FILE", ".env.credentials"),
        base_dir=base_dir,
    )
    if cred_file.exists():
        load_dotenv(dotenv_path=cred_file, override=True)
    return cred_file


# =========================
# EKSTRAKSI & PEMBERSIHAN DATA
# =========================

def unwrap_redirect_url(url: str) -> str:
    """Mengambil URL tujuan asli jika link dibungkus redirect (?u=...)."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "u" in query and query["u"]:
            return unquote(query["u"][0])
    except Exception:
        pass
    return url


def clean_external_url(url: str) -> str:
    """Membersihkan tanda baca di ujung URL dan menghapus parameter pelacak."""
    cleaned = url.strip().rstrip(".,;:!?)]}>\"'")
    if not cleaned:
        return ""

    try:
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        query_items = [
            (k, v)
            for k, v in parse_qs(parsed.query, keep_blank_values=False).items()
            if k.lower() not in TRACKING_QUERY_PARAMS
        ]
        new_query = urlencode(query_items, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))
    except Exception:
        return cleaned


def classify_link(url: str) -> str:
    """Mengelompokkan URL ke kategori yang mudah dibaca."""
    host = urlparse(url).netloc.lower()
    if host.startswith("chat.whatsapp.com"):
        return "Grup WhatsApp"
    if host == "wa.me" or host.endswith(".wa.me"):
        return "Kontak WhatsApp"
    if "drive.google.com" in host:
        return "Google Drive"
    if "docs.google.com" in host or "forms.gle" in host:
        return "Google Docs/Form"
    if host in {"t.me", "telegram.me", "www.t.me"}:
        return "Telegram"
    if "discord.gg" in host or ("discord.com" in host and "/invite/" in url.lower()):
        return "Discord"
    return "Link Lainnya"


def extract_relevant_links(text: str, hrefs: list[str] | None = None) -> list[tuple[str, str]]:
    """Mengekstrak link grup WA, Google Drive/Docs, Telegram, Discord, dan link eksternal lain."""
    candidates: list[str] = []
    if text:
        candidates.append(text)

    for href in hrefs or []:
        if href:
            unwrapped = unwrap_redirect_url(href)
            candidates.append(unwrapped or href)

    found: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for item in candidates:
        for match in WA_LINK_REGEX.finditer(item):
            normalized = f"https://chat.whatsapp.com/{match.group(1)}"
            if normalized not in seen_urls:
                seen_urls.add(normalized)
                found.append(("Grup WhatsApp", normalized))

    for item in candidates:
        raw_urls: list[str] = []
        for pattern, prefix in ((GENERAL_URL_REGEX, ""), (SCHEMELESS_URL_REGEX, "https://")):
            for match in pattern.finditer(item):
                trailing = item[match.end() : match.end() + 3]
                if trailing.startswith("…") or trailing == "...":
                    continue
                raw_urls.append(f"{prefix}{match.group(0)}")

        for raw_url in raw_urls:
            cleaned = clean_external_url(raw_url)
            if not cleaned:
                continue
            host = urlparse(cleaned).netloc.lower()
            if host in IGNORED_DOMAINS or "chat.whatsapp.com" in host:
                continue
            if cleaned not in seen_urls:
                seen_urls.add(cleaned)
                found.append((classify_link(cleaned), cleaned))

    return found


def extract_whatsapp_links(text: str, hrefs: list[str] | None = None) -> list[str]:
    """Helper khusus untuk mengambil hanya link grup WhatsApp dari teks dan href."""
    return [
        url for category, url in extract_relevant_links(text, hrefs) if category == "Grup WhatsApp"
    ]


def clean_post_text(raw_text: str, username: str = "", max_len: int = 320) -> str:
    """Membersihkan teks postingan dari noise UI agar nyaman dibaca."""
    if not raw_text:
        return "-"

    handle = username.lstrip("@").strip().lower()
    noise_exact = {
        "verified",
        "follow",
        "following",
        "translate",
        "see translation",
        "translate post",
        "view activity",
        "pinned",
        "author",
        "more",
        "show more",
    }

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    cleaned_lines: list[str] = []

    for idx, line in enumerate(lines):
        low = line.lower()
        if idx <= 2 and (
            (handle and low in {handle, f"@{handle}"})
            or re.fullmatch(r"\d+[smhdw]", low)
            or re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", low)
        ):
            continue
        if low in noise_exact:
            continue
        cleaned_lines.append(line)

    while cleaned_lines and re.fullmatch(r"[\d.,]+[KkMm]?", cleaned_lines[-1]):
        cleaned_lines.pop()

    joined = re.sub(r"\s+", " ", " | ".join(cleaned_lines)).strip()
    if not joined:
        return "-"
    if len(joined) > max_len:
        return joined[: max_len - 3].rstrip() + "..."
    return joined


def format_tanggal(raw_date: str) -> str:
    """Mengubah timestamp ISO menjadi format lokal YYYY-MM-DD HH:MM."""
    value = (raw_date or "").strip()
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def parse_sort_timestamp(tanggal: str) -> datetime:
    """Mengonversi teks tanggal menjadi objek datetime untuk pengurutan postingan terbaru."""
    value = (tanggal or "").strip()
    if not value or value == "-":
        return datetime.min

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        pass

    # Dukungan format waktu relatif singkat seperti 15m, 3h, 2d
    rel_match = re.fullmatch(r"(\d+)([smhdw])", value.lower())
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2)
        delta_map = {
            "s": timedelta(seconds=amount),
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }
        return datetime.now() - delta_map[unit]

    return datetime.min


def normalize_post_url(url: str) -> str:
    """Menghapus query/fragment pada URL postingan untuk perbandingan yang konsisten."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def posts_to_records(
    raw_posts: list[dict[str, Any]],
    keyword: str,
    parent_post_url: str = "",
) -> list[dict[str, str]]:
    """Mengubah data mentah DOM menjadi daftar record rapi beserta kategori link dan sumber."""
    records: list[dict[str, str]] = []
    normalized_parent = normalize_post_url(parent_post_url)

    for post in raw_posts:
        found_links = extract_relevant_links(post.get("teks", ""), post.get("hrefs", []))
        if not found_links:
            continue

        username = post.get("username", "") or "-"
        post_url = post.get("post_url", "")
        if normalized_parent:
            sumber = "Postingan" if normalize_post_url(post_url) == normalized_parent else "Komentar"
        else:
            sumber = "Postingan"

        tanggal = format_tanggal(post.get("tanggal", ""))
        ringkasan = clean_post_text(post.get("teks", ""), username=username)

        for kategori, link in found_links:
            records.append(
                {
                    "kategori": kategori,
                    "nama_grup": "-",
                    "link": link,
                    "username": username,
                    "tanggal": tanggal,
                    "sumber": sumber,
                    "keyword": keyword,
                    "ringkasan_info": ringkasan,
                    "post_url": post_url or parent_post_url,
                }
            )
    return records


def prioritize_comment_candidates(candidates: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """Mengurutkan postingan yang paling berpeluang memiliki link di komentar ke urutan teratas."""
    def _score(item: tuple[str, str, str]) -> tuple[int, int]:
        _, _, text = item
        low = text.lower()
        hits = sum(1 for hint in COMMENT_PRIORITY_HINTS if hint in low)
        return (-hits, 0)

    ranked = sorted(candidates, key=_score)
    return [(url, kw) for url, kw, _ in ranked]


# =========================
# FILTER KUALITAS & VALIDASI LINK WHATSAPP
# =========================

class _WhatsAppMetaParser(HTMLParser):
    """Parser HTML ringan untuk mengambil og:title, og:description, dan teks h3 pada halaman invite WA."""

    def __init__(self) -> None:
        super().__init__()
        self.og_title = ""
        self.og_description = ""
        self._in_h3 = False
        self._current_h3: list[str] = []
        self.h3_texts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "meta":
            prop = attr_map.get("property", "").lower() or attr_map.get("name", "").lower()
            content = html.unescape(attr_map.get("content", "")).strip()
            if prop == "og:title" and content:
                self.og_title = content
            elif prop == "og:description" and content:
                self.og_description = content
        elif tag.lower() == "h3":
            self._in_h3 = True
            self._current_h3 = []

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._current_h3.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "h3" and self._in_h3:
            text = html.unescape("".join(self._current_h3)).strip()
            if text:
                self.h3_texts.append(text)
            self._in_h3 = False
            self._current_h3 = []


def parse_whatsapp_invite_html(raw_html: str) -> tuple[bool, str]:
    """Menganalisis HTML halaman chat.whatsapp.com untuk memastikan grup aktif dan mengambil namanya."""
    parser = _WhatsAppMetaParser()
    parser.feed(raw_html or "")

    candidate_name = parser.og_title.strip()
    if not candidate_name:
        for h3 in parser.h3_texts:
            if "don't have whatsapp" not in h3.lower():
                candidate_name = h3
                break

    low_name = candidate_name.lower().strip()
    if not low_name or low_name in INVALID_WA_TITLES:
        return False, ""

    invalid_fragments = ("expired", "revoked", "reset", "couldn't find")
    if any(frag in low_name for frag in invalid_fragments):
        return False, ""

    return True, candidate_name


def check_whatsapp_invite(url: str, timeout: float = 10.0) -> tuple[bool, str, str]:
    """Mengecek validitas link chat.whatsapp.com via HTTP GET dan mengembalikan (valid, nama_grup, alasan)."""
    if not url.startswith("https://chat.whatsapp.com/"):
        return False, "", "bukan_link_wa"

    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Encoding": "identity",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        with urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return False, "", f"http_{getattr(resp, 'status', 0)}"
            raw_html = resp.read(200_000).decode("utf-8", errors="ignore")
            is_valid, group_name = parse_whatsapp_invite_html(raw_html)
            return is_valid, group_name, ("valid" if is_valid else "mati_atau_direset")
    except HTTPError as err:
        return False, "", f"http_{err.code}"
    except (URLError, TimeoutError, ValueError) as exc:
        return False, "", f"network_error:{exc}"


def is_spam_text(text: str, spam_keywords: list[str]) -> bool:
    """Mengecek apakah teks mengandung kata spam (dengan batas kata agar akurat)."""
    if not text or not spam_keywords:
        return False
    escaped = [re.escape(kw.strip()) for kw in spam_keywords if kw.strip()]
    if not escaped:
        return False
    pattern = re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)
    return bool(pattern.search(text))


def is_relevant_topic(text: str, topic_keywords: list[str]) -> bool:
    """Mengecek apakah teks atau nama grup relevan dengan minimal satu keyword topik."""
    if not topic_keywords:
        return True
    low = (text or "").lower()
    for kw in topic_keywords:
        token = kw.strip().lower()
        if not token:
            continue
        if len(token) <= 3:
            if re.search(rf"\b{re.escape(token)}\b", low):
                return True
        elif token in low:
            return True
    return False


def deduplicate_and_sort_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Menghapus duplikat URL lalu mengurutkan dari postingan terbaru."""
    sorted_input = sorted(
        records,
        key=lambda r: parse_sort_timestamp(r.get("tanggal", "")),
        reverse=True,
    )

    seen_links: set[str] = set()
    unique_records: list[dict[str, str]] = []
    for record in sorted_input:
        link = record.get("link", "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique_records.append(record)

    for idx, record in enumerate(unique_records, start=1):
        record["no"] = str(idx)
    return unique_records


def filter_and_validate_records(
    records: list[dict[str, str]],
    topic_keywords: list[str],
    spam_keywords: list[str],
    validate_wa: bool = True,
    wa_delay_min: float = DEFAULT_WA_CHECK_DELAY_MIN,
    wa_delay_max: float = DEFAULT_WA_CHECK_DELAY_MAX,
    max_wa_checks: int = DEFAULT_MAX_WA_CHECKS,
    only_whatsapp: bool = False,
    wa_checker: Any = None,
) -> list[dict[str, str]]:
    """Menjalankan filter duplikat, urut terbaru, anti-spam, validasi WA berjeda, dan relevansi topik."""
    checker = wa_checker or check_whatsapp_invite
    unique_records = deduplicate_and_sort_records(records)

    stats = {
        "awal": len(records),
        "unik": len(unique_records),
        "spam": 0,
        "non_wa_dilewati": 0,
        "wa_mati": 0,
        "tidak_relevan": 0,
        "lolos": 0,
    }

    passed_records: list[dict[str, str]] = []
    wa_checks_done = 0

    for record in unique_records:
        kategori = record.get("kategori", "Link Lainnya")
        link = record.get("link", "")
        combined_post_text = f"{record.get('ringkasan_info', '')} {record.get('username', '')}"

        if only_whatsapp and kategori != "Grup WhatsApp":
            stats["non_wa_dilewati"] += 1
            continue

        # Filter anti-spam awal pada teks postingan dan akun
        if is_spam_text(combined_post_text, spam_keywords):
            stats["spam"] += 1
            continue

        nama_grup = record.get("nama_grup", "-") or "-"

        # Cek validitas link chat.whatsapp.com secara pelan-pelan dan ambil nama grupnya
        if kategori == "Grup WhatsApp" and validate_wa:
            if wa_checks_done >= max_wa_checks:
                print(f"[INFO] Batas {max_wa_checks} cek link WA per sesi tercapai.")
                break

            if wa_checks_done > 0:
                delay = random.uniform(wa_delay_min, wa_delay_max)
                print(f"[INFO] Jeda cek WA {delay:.2f}s sebelum memeriksa link berikutnya...")
                time.sleep(delay)

            wa_checks_done += 1
            is_valid, fetched_name, reason = checker(link)
            if not is_valid:
                print(f"[DROP] Link WA tidak valid ({reason}): {link}")
                stats["wa_mati"] += 1
                continue

            nama_grup = fetched_name or "-"
            record["nama_grup"] = nama_grup
            print(f"[OK] Link WA aktif: {nama_grup} -> {link}")

            # Cek anti-spam lagi terhadap nama grup WhatsApp yang baru diambil
            if is_spam_text(nama_grup, spam_keywords):
                print(f"[DROP] Nama grup terdeteksi spam: {nama_grup}")
                stats["spam"] += 1
                continue

        # Filter relevansi topik terhadap teks postingan dan nama grup
        relevance_text = f"{record.get('ringkasan_info', '')} {nama_grup}"
        if not is_relevant_topic(relevance_text, topic_keywords):
            stats["tidak_relevan"] += 1
            continue

        passed_records.append(record)

    # Pastikan urutan akhir tetap dari postingan terbaru dengan nomor urut rapi 1..N
    passed_records.sort(key=lambda r: parse_sort_timestamp(r.get("tanggal", "")), reverse=True)
    for idx, rec in enumerate(passed_records, start=1):
        rec["no"] = str(idx)
    stats["lolos"] = len(passed_records)

    print(
        "\n[FILTER] Ringkasan seleksi -> "
        f"mentah={stats['awal']} | unik={stats['unik']} | spam={stats['spam']} "
        f"| wa_mati={stats['wa_mati']} | tidak_relevan={stats['tidak_relevan']} "
        f"| lolos={stats['lolos']}"
    )
    return passed_records


def apply_quality_filters_from_env(
    records: list[dict[str, str]],
    search_keywords: list[str],
    wa_checker: Any = None,
) -> list[dict[str, str]]:
    """Membaca konfigurasi filter kualitas dari .env lalu menjalankan seluruh filter & validasi WA."""
    topic_keywords = derive_topic_keywords(os.getenv("TOPIC_KEYWORDS"), search_keywords)
    spam_keywords = get_spam_keywords(os.getenv("SPAM_KEYWORDS"))
    validate_wa = os.getenv("VALIDATE_WA_LINKS", "true").strip().lower() == "true"
    only_whatsapp = os.getenv("ONLY_WHATSAPP_LINKS", "false").strip().lower() == "true"
    wa_delay_min = parse_float_env("WA_CHECK_DELAY_MIN", DEFAULT_WA_CHECK_DELAY_MIN, minimum=0.5)
    wa_delay_max = max(wa_delay_min, parse_float_env("WA_CHECK_DELAY_MAX", DEFAULT_WA_CHECK_DELAY_MAX, minimum=1.0))
    max_wa_checks = parse_int_env("MAX_WA_CHECKS_PER_SESSION", DEFAULT_MAX_WA_CHECKS, minimum=1)

    print(f"[FILTER] Kata topik aktif: {topic_keywords}")
    return filter_and_validate_records(
        records,
        topic_keywords=topic_keywords,
        spam_keywords=spam_keywords,
        validate_wa=validate_wa,
        wa_delay_min=wa_delay_min,
        wa_delay_max=wa_delay_max,
        max_wa_checks=max_wa_checks,
        only_whatsapp=only_whatsapp,
        wa_checker=wa_checker,
    )


def save_to_csv(records: list[dict[str, str]], output_path: Path) -> None:
    """Menyimpan hasil berkualitas yang lolos filter ke file CSV (UTF-8 BOM)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "no",
        "tanggal",
        "kategori",
        "nama_grup",
        "link",
        "username",
        "sumber",
        "keyword",
        "ringkasan_info",
        "post_url",
    ]
    with output_path.open("w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for idx, record in enumerate(records, start=1):
            row = {key: record.get(key, "") for key in fieldnames}
            row["no"] = record.get("no") or str(idx)
            row["nama_grup"] = row["nama_grup"] or "-"
            writer.writerow(row)


def save_to_markdown(records: list[dict[str, str]], md_path: Path, platform_name: str = "Threads") -> None:
    """Menyimpan hasil yang lolos filter dalam tabel Markdown terurut dari terbaru."""
    lines = [
        f"# Hasil Pencarian Grup & Referensi {platform_name}",
        "",
        f"Total link berkualitas yang lolos filter: **{len(records)}** (diurutkan dari terbaru)",
        "",
        "| No | Tanggal | Kategori | Nama Grup | Link | Akun | Sumber | Ringkasan Info |",
        "|---:|---|---|---|---|---|---|---|",
    ]

    for idx, item in enumerate(records, start=1):
        tanggal = item.get("tanggal", "-")
        kategori = item.get("kategori", "-")
        nama_grup = (item.get("nama_grup", "-") or "-").replace("|", "/")
        link = item.get("link", "")
        username = item.get("username", "-")
        sumber = item.get("sumber", "-")
        info = item.get("ringkasan_info", "-").replace("|", "/")
        post_url = item.get("post_url", "")
        akun_cell = f"[{username}]({post_url})" if post_url else username
        lines.append(
            f"| {idx} | {tanggal} | {kategori} | {nama_grup} | {link} | {akun_cell} | {sumber} | {info} |"
        )
    lines.append("")

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")


def print_summary(records: list[dict[str, str]], csv_path: Path, md_path: Path) -> None:
    """Menampilkan ringkasan hasil akhir yang lolos filter di terminal."""
    counts: dict[str, int] = {}
    for rec in records:
        kat = rec.get("kategori", "Link Lainnya")
        counts[kat] = counts.get(kat, 0) + 1

    print("\n================ RINGKASAN HASIL LOLOS FILTER ================")
    print(f"Total link lolos: {len(records)}")
    for kat, total in counts.items():
        print(f"  - {kat:<16}: {total}")
    print(f"File CSV        : {csv_path.resolve()}")
    print(f"File Markdown   : {md_path.resolve()}")
    print("==============================================================\n")


# =========================
# PLAYWRIGHT OPTIMASI & SCROLL HELPER
# =========================

def enable_fast_routing(context: BrowserContext) -> None:
    """Memblokir gambar, video, dan font agar loading halaman jauh lebih cepat."""
    blocked_types = {"image", "media", "font"}

    def _route_handler(route: Any) -> None:
        if route.request.resource_type in blocked_types:
            route.abort()
        else:
            route.continue_()

    context.route("**/*", _route_handler)


def wait_for_posts_ready(page: Page, selector: str) -> None:
    """Menunggu elemen postingan selesai dirender oleh SPA."""
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(1500)


def scroll_and_collect(
    page: Page,
    extractor: Any,
    max_scrolls: int,
    label: str,
    delay_min: float,
    delay_max: float,
) -> tuple[list[dict[str, Any]], int]:
    """Melakukan scroll hingga max_scrolls dengan delay acak dan mengumpulkan item unik di halaman."""
    seen_items: set[tuple[str, str, str]] = set()
    collected_raw: list[dict[str, Any]] = []
    scrolls_used = 0
    previous_height = 0
    stagnant_rounds = 0

    for step in range(max_scrolls + 1):
        raw_posts = extractor(page)
        new_count = 0
        for post in raw_posts:
            key = (
                normalize_post_url(post.get("post_url", "")),
                post.get("username", ""),
                clean_post_text(post.get("teks", ""), max_len=100),
            )
            if key not in seen_items:
                seen_items.add(key)
                collected_raw.append(post)
                new_count += 1

        print(f"[INFO] [{label}] Terbaca: {len(raw_posts)} | Item baru: {new_count}")

        if step == max_scrolls:
            break

        try:
            current_height = page.evaluate(
                "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
            )
            page.evaluate(
                "() => window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))"
            )
            scrolls_used += 1

            delay = random.uniform(delay_min, delay_max)
            print(f"[INFO] Scroll #{scrolls_used} ({label}) | jeda {delay:.2f}s...")
            time.sleep(delay)

            new_height = page.evaluate(
                "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
            )
            if new_height == current_height and new_height == previous_height:
                stagnant_rounds += 1
                if stagnant_rounds >= 2:
                    print(f"[INFO] Tidak ada item baru untuk '{label}', lanjut.")
                    break
            else:
                stagnant_rounds = 0
            previous_height = new_height
        except Exception as exc:
            print(f"[WARN] Terjadi error saat scroll ({label}): {exc}")
            break

    return collected_raw, scrolls_used
