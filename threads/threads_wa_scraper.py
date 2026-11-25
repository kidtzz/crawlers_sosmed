"""Skrip Playwright untuk mencari dan mengekstrak link grup WA, Google Drive, dan referensi belajar dari Threads."""

import os
import random
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

# Pastikan root workspace dapat diimpor baik saat dijalankan dari root maupun dari subfolder threads/
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from crawler_utils import (  # noqa: E402
    DEFAULT_KEYWORDS,
    DEFAULT_MAX_COMMENT_POSTS,
    DEFAULT_MAX_COMMENT_SCROLLS,
    DEFAULT_MAX_SCROLLS_PER_KEYWORD,
    DEFAULT_MAX_SCROLLS_PER_SESSION,
    DEFAULT_SCROLL_DELAY_MAX,
    DEFAULT_SCROLL_DELAY_MIN,
    clean_post_text,
    deduplicate_and_sort_records,
    enable_fast_routing,
    extract_relevant_links,
    extract_whatsapp_links,
    format_tanggal,
    load_environment as _shared_load_environment,
    normalize_post_url,
    parse_float_env,
    parse_int_env,
    parse_keywords,
    posts_to_records,
    print_summary,
    prioritize_comment_candidates,
    resolve_project_path as _shared_resolve_project_path,
    save_to_csv,
    save_to_markdown,
    scroll_and_collect,
    wait_for_posts_ready,
)

try:
    from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ImportError:  # Memungkinkan impor fungsi helper untuk pengujian tanpa Playwright
    BrowserContext = Any  # type: ignore[assignment, misc]
    Page = Any  # type: ignore[assignment, misc]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment, misc]
    sync_playwright = None


# =========================
# 1. KONFIGURASI THREADS
# =========================

BASE_URL = "https://www.threads.com"
POST_SELECTOR = 'div[data-pressable-container="true"], article'


def resolve_project_path(raw_path: str, base_dir: Path = BASE_DIR) -> Path:
    """Mengubah path relatif menjadi path di dalam folder threads/."""
    return _shared_resolve_project_path(raw_path, base_dir=base_dir)


def load_environment(
    env_path: Path = BASE_DIR / ".env",
    credentials_path: Path | None = None,
) -> Path:
    """Memuat konfigurasi .env dan kredensial .env.credentials dari folder threads/."""
    return _shared_load_environment(base_dir=BASE_DIR, env_path=env_path, credentials_path=credentials_path)


# =========================
# 2. AUTENTIKASI & STORAGE STATE THREADS
# =========================

def has_session_cookies(context: BrowserContext) -> bool:
    """Mengecek apakah browser memiliki cookie sesi Threads/Instagram yang aktif."""
    try:
        cookies = context.cookies()
        session_names = {"sessionid", "ds_user_id"}
        return any(
            c.get("name") in session_names and bool(c.get("value"))
            for c in cookies
        )
    except Exception:
        return False


def is_logged_in(page: Page, context: BrowserContext) -> bool:
    """Mengecek apakah sesi saat ini benar-benar sudah login di Threads."""
    if not has_session_cookies(context):
        return False

    try:
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(1200)

        current_url = page.url.lower()
        if "/login" in current_url:
            return False

        # Deteksi modal atau kartu ajakan login yang muncul saat belum login
        logged_out_texts = [
            "Log in or sign up for Threads",
            "Log in with username instead",
            "Say more with Threads",
        ]
        for text in logged_out_texts:
            loc = page.get_by_text(text, exact=False)
            if loc.count() > 0 and loc.first.is_visible():
                return False

        login_input = page.locator('input[name="username"], input[autocomplete="username"]')
        if login_input.count() > 0 and login_input.first.is_visible():
            return False

        return has_session_cookies(context)
    except Exception as exc:
        print(f"[WARN] Gagal memeriksa status login: {exc}")
        return False


def login_threads(page: Page, context: BrowserContext, username: str, password: str, state_path: Path) -> None:
    """Login ke Threads menggunakan akun cadangan lalu menyimpan storage_state."""
    print("[INFO] Memulai proses login ke Threads...")
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    try:
        username_selector = (
            'input[name="username"], input[name="email"], '
            'input[autocomplete="username"], input[placeholder*="Username"], '
            'input[placeholder*="username"]'
        )
        password_selector = 'input[name="password"], input[autocomplete="current-password"], input[type="password"]'

        # Jika halaman /login menampilkan opsi "Log in with username instead", klik terlebih dahulu
        if page.locator(password_selector).count() == 0:
            username_instead = page.get_by_text("Log in with username instead", exact=False)
            if username_instead.count() > 0 and username_instead.first.is_visible():
                print("[INFO] Mengklik 'Log in with username instead'...")
                username_instead.first.click()
                page.wait_for_timeout(1500)

        username_locator = page.locator(username_selector).first
        password_locator = page.locator(password_selector).first

        username_locator.wait_for(state="visible", timeout=15000)
        password_locator.wait_for(state="visible", timeout=15000)

        username_locator.fill(username)
        password_locator.fill(password)

        submit_btn = page.locator(
            'button[type="submit"], div[role="button"]:text-is("Log in"), button:text-is("Log in")'
        ).first
        if submit_btn.count() > 0 and submit_btn.is_visible():
            submit_btn.click()
        else:
            password_locator.press("Enter")

        print("[INFO] Menunggu verifikasi login selesai (maks. 90 detik)...")
        deadline = time.time() + 90
        while time.time() < deadline:
            not_now_btn = page.locator('div[role="button"]:text-is("Not now"), button:text-is("Not now")')
            if not_now_btn.count() > 0 and not_now_btn.first.is_visible():
                not_now_btn.first.click()
                page.wait_for_timeout(1000)

            if has_session_cookies(context) and "/login" not in page.url.lower():
                break
            page.wait_for_timeout(1200)
        else:
            raise PlaywrightTimeoutError("Cookie sesi login tidak terbentuk dalam 90 detik.")

        page.wait_for_timeout(1500)
        context.storage_state(path=str(state_path))
        print(f"[INFO] Login berhasil! Sesi disimpan ke {state_path}")
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "Timeout saat menunggu login selesai. Pastikan kredensial di .env.credentials benar. "
            "Jika muncul verifikasi/CAPTCHA/2FA, jalankan dengan HEADLESS=false dan selesaikan verifikasi secara manual."
        ) from exc


# =========================
# 3. PARSING DOM & SCRAPING THREADS
# =========================

def extract_visible_posts(page: Page) -> list[dict[str, Any]]:
    """Mengambil data mentah postingan yang sedang tampil di DOM Threads."""
    js_extract = """
    () => {
        const containers = Array.from(
            document.querySelectorAll('div[data-pressable-container="true"], article')
        );
        const results = [];

        for (const el of containers) {
            const timeEl = el.querySelector('time');
            const userLink = el.querySelector('a[href^="/@"]');
            if (!timeEl && !userLink) continue;

            let username = "";
            if (userLink) {
                const href = userLink.getAttribute('href') || "";
                const match = href.match(/^\\/@([^/?#]+)/);
                username = match ? `@${match[1]}` : (userLink.innerText || "").trim();
            }

            const tanggal = timeEl
                ? (timeEl.getAttribute('datetime') || timeEl.innerText || "").trim()
                : "";

            const postLinkEl = el.querySelector('a[href*="/post/"]');
            const postUrl = postLinkEl ? postLinkEl.href : "";

            const hrefs = Array.from(el.querySelectorAll('a[href]'))
                .map(a => a.href || a.getAttribute('href') || "")
                .filter(Boolean);

            const teks = (el.innerText || "").trim();

            if (teks || hrefs.length > 0) {
                results.push({
                    username,
                    tanggal,
                    teks,
                    hrefs,
                    post_url: postUrl
                });
            }
        }
        return results;
    }
    """
    try:
        return page.evaluate(js_extract)
    except Exception as exc:
        print(f"[WARN] Gagal membaca DOM postingan: {exc}")
        return []


def collect_posts_for_keyword(
    page: Page,
    keyword: str,
    max_scrolls: int,
    delay_min: float,
    delay_max: float,
) -> tuple[list[dict[str, str]], list[tuple[str, str, str]], int]:
    """Mencari keyword di Threads, melakukan scroll, dan mengembalikan record + kandidat komentar."""
    search_url = f"{BASE_URL}/search?q={quote_plus(keyword)}&serp_type=default"
    print(f"\n[INFO] Mencari keyword: '{keyword}'")

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        wait_for_posts_ready(page, POST_SELECTOR)
    except Exception as exc:
        print(f"[ERROR] Gagal membuka pencarian '{keyword}': {exc}")
        return [], [], 0

    raw_posts, scrolls_used = scroll_and_collect(
        page, extract_visible_posts, max_scrolls, keyword, delay_min, delay_max
    )
    records = posts_to_records(raw_posts, keyword)

    candidate_info: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    for post in raw_posts:
        url = post.get("post_url", "")
        norm = normalize_post_url(url)
        if url and norm not in seen_urls:
            seen_urls.add(norm)
            candidate_info.append((url, keyword, post.get("teks", "")))

    print(f"[INFO] Link ditemukan dari '{keyword}': {len(records)} | Kandidat post: {len(candidate_info)}")
    return records, candidate_info, scrolls_used


def collect_comments_for_post(
    page: Page,
    post_url: str,
    keyword: str,
    max_scrolls: int,
    delay_min: float,
    delay_max: float,
) -> tuple[list[dict[str, str]], int]:
    """Membuka halaman detail postingan untuk mengekstrak link dari komentar/balasan."""
    print(f"[INFO] Cek komentar: {post_url}")
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=25000)
        wait_for_posts_ready(page, POST_SELECTOR)

        more_replies = page.get_by_text("View more replies", exact=False)
        if more_replies.count() > 0 and more_replies.first.is_visible():
            more_replies.first.click()
            page.wait_for_timeout(1000)
    except Exception as exc:
        print(f"[WARN] Gagal membuka detail postingan {post_url}: {exc}")
        return [], 0

    raw_items, scrolls_used = scroll_and_collect(
        page, extract_visible_posts, max_scrolls, "komentar", delay_min, delay_max
    )
    records = posts_to_records(raw_items, keyword, parent_post_url=post_url)
    print(f"[INFO] Link ditemukan di komentar/post: {len(records)}")
    return records, scrolls_used


# =========================
# 4. FUNGSI UTAMA (ORKESTRASI)
# =========================

def main() -> None:
    """Menjalankan alur lengkap scraper Threads: muat env, cek/simpan sesi, scraping cepat, dan ekspor."""
    if sync_playwright is None:
        raise ImportError(
            "Library 'playwright' belum terpasang. Jalankan: pip install -r requirements.txt && playwright install chromium"
        )

    cred_file = load_environment()
    username = os.getenv("THREADS_USERNAME", "").strip()
    password = os.getenv("THREADS_PASSWORD", "").strip()
    keywords = parse_keywords(os.getenv("THREADS_KEYWORDS"))

    headless = os.getenv("HEADLESS", "false").strip().lower() == "true"
    block_media = os.getenv("BLOCK_MEDIA", "true").strip().lower() == "true"
    scrape_comments = os.getenv("SCRAPE_COMMENTS", "true").strip().lower() == "true"

    delay_min = parse_float_env("SCROLL_DELAY_MIN", DEFAULT_SCROLL_DELAY_MIN, minimum=0.3)
    delay_max = max(delay_min, parse_float_env("SCROLL_DELAY_MAX", DEFAULT_SCROLL_DELAY_MAX, minimum=0.5))
    max_scrolls_per_session = parse_int_env("MAX_SCROLLS_PER_SESSION", DEFAULT_MAX_SCROLLS_PER_SESSION, minimum=1)
    max_scrolls_per_keyword = parse_int_env("MAX_SCROLLS_PER_KEYWORD", DEFAULT_MAX_SCROLLS_PER_KEYWORD, minimum=1)
    max_comment_posts = parse_int_env("MAX_COMMENT_POSTS_PER_SESSION", DEFAULT_MAX_COMMENT_POSTS, minimum=0)
    max_comment_scrolls = parse_int_env("MAX_COMMENT_SCROLLS_PER_POST", DEFAULT_MAX_COMMENT_SCROLLS, minimum=0)

    state_path = resolve_project_path(os.getenv("STORAGE_STATE_FILE", "storage_state.json"))
    output_csv = resolve_project_path(os.getenv("OUTPUT_CSV", "hasil_grup_wa.csv"))
    output_md = output_csv.with_suffix(".md")

    print(f"[INFO] Keyword aktif ({len(keywords)}): {keywords}")
    print(
        f"[INFO] Mode cepat: block_media={block_media} | delay={delay_min:.1f}-{delay_max:.1f}s "
        f"| batas_scroll={max_scrolls_per_session}"
    )

    all_records: list[dict[str, str]] = []
    candidate_pool: list[tuple[str, str, str]] = []
    seen_candidate_urls: set[str] = set()
    total_scrolls = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            has_state_file = state_path.exists()
            if has_state_file:
                print(f"[INFO] Memuat sesi tersimpan dari {state_path}...")
                context = browser.new_context(
                    storage_state=str(state_path),
                    viewport={"width": 1280, "height": 800},
                )
            else:
                print("[INFO] File sesi belum ada, membuat sesi browser baru...")
                context = browser.new_context(viewport={"width": 1280, "height": 800})

            page = context.new_page()

            if has_state_file and is_logged_in(page, context):
                print("[INFO] Sesi login masih aktif, melewati proses login.")
            else:
                if has_state_file:
                    print("[INFO] Sesi tersimpan sudah kedaluwarsa, melakukan login ulang...")
                if not username or not password:
                    raise ValueError(
                        f"THREADS_USERNAME dan THREADS_PASSWORD wajib diisi di {cred_file} (atau .env) untuk login."
                    )
                login_threads(page, context, username, password, state_path)

            if block_media:
                enable_fast_routing(context)
                print("[INFO] Blokir gambar/media/font aktif untuk mempercepat crawling.")

            for keyword in keywords:
                remaining_scrolls = max_scrolls_per_session - total_scrolls
                if remaining_scrolls <= 0:
                    print(f"[INFO] Batas maksimal {max_scrolls_per_session} scroll per sesi tercapai.")
                    break

                keyword_scroll_budget = min(remaining_scrolls, max_scrolls_per_keyword)
                records, post_candidates, used = collect_posts_for_keyword(
                    page, keyword, keyword_scroll_budget, delay_min, delay_max
                )
                all_records.extend(records)
                total_scrolls += used

                for url, kw, text in post_candidates:
                    norm = normalize_post_url(url)
                    if norm and norm not in seen_candidate_urls:
                        seen_candidate_urls.add(norm)
                        candidate_pool.append((url, kw, text))

                time.sleep(random.uniform(0.5, 1.0))

            if scrape_comments and max_comment_posts > 0 and candidate_pool:
                posts_to_check = prioritize_comment_candidates(candidate_pool)[:max_comment_posts]
                print(f"\n[INFO] Memeriksa komentar pada {len(posts_to_check)} postingan prioritas...")

                for post_url, keyword in posts_to_check:
                    remaining_scrolls = max_scrolls_per_session - total_scrolls
                    comment_scroll_budget = min(max_comment_scrolls, max(0, remaining_scrolls))

                    comment_records, used = collect_comments_for_post(
                        page, post_url, keyword, comment_scroll_budget, delay_min, delay_max
                    )
                    all_records.extend(comment_records)
                    total_scrolls += used

                    time.sleep(random.uniform(0.5, 1.0))

        except KeyboardInterrupt:
            print("\n[WARN] Proses dihentikan pengguna. Menyimpan data yang sudah terkumpul...")
        except Exception as exc:
            print(f"[ERROR] Terjadi kesalahan selama scraping: {exc}")
        finally:
            browser.close()

    unique_records = deduplicate_and_sort_records(all_records)
    if unique_records:
        save_to_csv(unique_records, output_csv)
        save_to_markdown(unique_records, output_md, platform_name="Threads")
        print_summary(unique_records, output_csv, output_md)
    else:
        print("\n[SELESAI] Tidak ada link grup/referensi yang ditemukan pada sesi ini.")


if __name__ == "__main__":
    main()
