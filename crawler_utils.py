"""Utilitas bersama untuk crawler Playwright (ekstraksi link, pembersihan teks, sesi, scroll, & ekspor)."""

import csv
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from dotenv import load_dotenv

try:
    from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
except ImportError:  # Memungkinkan impor fungsi helper untuk pengujian tanpa Playwright
    BrowserContext = Any  # type: ignore[assignment, misc]
    Page = Any  # type: ignore[assignment, misc]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment, misc]


# =========================
# KONSTANTA BERSAMA
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

# Regex link grup WhatsApp, URL penuh, dan domain populer tanpa skema http(s) (umum di X/Twitter)
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

# Domain internal platform yang diabaikan sebagai link referensi eksternal
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

CATEGORY_PRIORITY = {
    "Grup WhatsApp": 0,
    "Kontak WhatsApp": 1,
    "Google Drive": 2,
    "Google Docs/Form": 3,
    "Telegram": 4,
    "Discord": 5,
    "Link Lainnya": 6,
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


def parse_keywords(raw_keywords: str | None, default_keywords: list[str] | None = None) -> list[str]:
    """Mengurai daftar keyword dipisahkan koma dari .env, atau memakai default."""
    fallback = list(default_keywords or DEFAULT_KEYWORDS)
    if not raw_keywords or not raw_keywords.strip():
        return fallback

    parsed = [kw.strip() for kw in raw_keywords.split(",") if kw.strip()]
    unique_keywords = list(dict.fromkeys(parsed))
    return unique_keywords if unique_keywords else fallback


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

    # Prioritaskan link grup WhatsApp agar ternormalisasi rapi
    for item in candidates:
        for match in WA_LINK_REGEX.finditer(item):
            normalized = f"https://chat.whatsapp.com/{match.group(1)}"
            if normalized not in seen_urls:
                seen_urls.add(normalized)
                found.append(("Grup WhatsApp", normalized))

    # Ekstrak URL penuh maupun domain populer tanpa skema (sering muncul di teks X/Twitter)
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


def deduplicate_and_sort_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Menghapus duplikat berdasarkan URL dan mengurutkan berdasarkan kategori lalu tanggal terbaru."""
    seen_links: set[str] = set()
    unique_records: list[dict[str, str]] = []

    for record in records:
        link = record.get("link", "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique_records.append(record)

    unique_records.sort(key=lambda r: r.get("tanggal", ""), reverse=True)
    unique_records.sort(key=lambda r: CATEGORY_PRIORITY.get(r.get("kategori", "Link Lainnya"), 99))

    for idx, record in enumerate(unique_records, start=1):
        record["no"] = str(idx)

    return unique_records


def save_to_csv(records: list[dict[str, str]], output_path: Path) -> None:
    """Menyimpan hasil ekstraksi yang sudah dirapikan ke file CSV (UTF-8 BOM)."""
    fieldnames = [
        "no",
        "kategori",
        "link",
        "username",
        "tanggal",
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
            writer.writerow(row)


def save_to_markdown(records: list[dict[str, str]], md_path: Path, platform_name: str = "Threads") -> None:
    """Menyimpan ringkasan hasil dalam tabel Markdown agar nyaman dibaca."""
    lines = [
        f"# Hasil Pencarian Grup & Referensi {platform_name}",
        "",
        f"Total link unik ditemukan: **{len(records)}**",
        "",
    ]

    grouped: dict[str, list[dict[str, str]]] = {}
    for rec in records:
        grouped.setdefault(rec.get("kategori", "Link Lainnya"), []).append(rec)

    for kategori, items in grouped.items():
        lines.append(f"## {kategori} ({len(items)})")
        lines.append("")
        lines.append("| No | Link | Akun | Tanggal | Sumber | Ringkasan Info |")
        lines.append("|---:|---|---|---|---|---|")
        for idx, item in enumerate(items, start=1):
            link = item.get("link", "")
            username = item.get("username", "-")
            tanggal = item.get("tanggal", "-")
            sumber = item.get("sumber", "-")
            info = item.get("ringkasan_info", "-").replace("|", "/")
            post_url = item.get("post_url", "")
            akun_cell = f"[{username}]({post_url})" if post_url else username
            lines.append(f"| {idx} | {link} | {akun_cell} | {tanggal} | {sumber} | {info} |")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")


def print_summary(records: list[dict[str, str]], csv_path: Path, md_path: Path) -> None:
    """Menampilkan ringkasan hasil scraping yang rapi per kategori di terminal."""
    counts: dict[str, int] = {}
    for rec in records:
        kat = rec.get("kategori", "Link Lainnya")
        counts[kat] = counts.get(kat, 0) + 1

    print("\n================ RINGKASAN HASIL ================")
    print(f"Total link unik : {len(records)}")
    for kat, total in counts.items():
        print(f"  - {kat:<16}: {total}")
    print(f"File CSV        : {csv_path.resolve()}")
    print(f"File Markdown   : {md_path.resolve()}")
    print("=================================================\n")


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
