"""Skrip Playwright untuk mencari dan mengekstrak link grup WA, Google Drive, dan referensi belajar dari Twitter (X)."""

import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Pastikan root workspace dapat diimpor baik saat dijalankan dari root maupun dari subfolder twitter/
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.crawler_utils import (  # noqa: E402
    DEFAULT_KEYWORDS,
    DEFAULT_MAX_COMMENT_POSTS,
    DEFAULT_MAX_COMMENT_SCROLLS,
    DEFAULT_MAX_SCROLLS_PER_KEYWORD,
    DEFAULT_MAX_SCROLLS_PER_SESSION,
    DEFAULT_SCROLL_DELAY_MAX,
    DEFAULT_SCROLL_DELAY_MIN,
    apply_quality_filters_from_env,
    deduplicate_and_sort_records,
    enable_fast_routing,
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
# 1. KONFIGURASI TWITTER (X)
# =========================

BASE_URL = "https://x.com"
TWEET_SELECTOR = 'article[data-testid="tweet"]'

# Domain target untuk mendeteksi kapan link t.co yang terpotong perlu di-resolve
TCO_RESOLVE_HINTS = (
    "chat.whatsapp.com",
    "wa.me",
    "drive.google.com",
    "docs.google.com",
    "forms.gle",
    "t.me",
    "telegram.me",
    "discord.gg",
    "discord.com",
    "notion.",
    "bit.ly",
    "linktr.ee",
    "s.id",
)

_TCO_CACHE: dict[str, str] = {}


class _NoRedirectHandler(HTTPRedirectHandler):
    """Menahan redirect HTTP agar header Location t.co dapat dibaca langsung tanpa mengunduh halaman tujuan."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def resolve_project_path(raw_path: str, base_dir: Path = BASE_DIR) -> Path:
    """Mengubah path relatif menjadi path di dalam folder twitter/."""
    return _shared_resolve_project_path(raw_path, base_dir=base_dir)


def load_environment(
    env_path: Path = BASE_DIR / ".env",
    credentials_path: Path | None = None,
) -> Path:
    """Memuat konfigurasi .env dan kredensial .env.credentials dari folder twitter/."""
    return _shared_load_environment(base_dir=BASE_DIR, env_path=env_path, credentials_path=credentials_path)


def build_search_url(keyword: str, search_mode: str = "live") -> str:
    """Membentuk URL pencarian X berdasarkan keyword dan mode tab (live/top)."""
    encoded = quote_plus(keyword)
    mode = search_mode.strip().lower()
    if mode == "top":
        return f"{BASE_URL}/search?q={encoded}&src=typed_query"
    return f"{BASE_URL}/search?q={encoded}&src=typed_query&f=live"


# =========================
# 2. RESOLUSI LINK T.CO TERPOTONG
# =========================

def should_resolve_tco(anchor_text: str, tweet_text: str) -> bool:
    """Menentukan apakah shortlink t.co perlu di-resolve agar tidak memperlambat crawler."""
    text_low = (anchor_text or "").lower()
    tweet_low = (tweet_text or "").lower()

    has_truncation = "…" in anchor_text or anchor_text.endswith("...")
    if any(hint in text_low for hint in TCO_RESOLVE_HINTS):
        return has_truncation

    return any(cue in tweet_low for cue in ("grup", "whatsapp", "drive.google", "docs.google", "link grup"))


def resolve_tco_url(tco_url: str, timeout: float = 3.5) -> str:
    """Mengambil URL tujuan dari shortlink t.co menggunakan redirect/meta refresh."""
    if not tco_url.startswith(("http://t.co/", "https://t.co/")):
        return tco_url
    if tco_url in _TCO_CACHE:
        return _TCO_CACHE[tco_url]

    opener = build_opener(_NoRedirectHandler())
    req = Request(tco_url, headers={"User-Agent": "Mozilla/5.0"})
    resolved = tco_url

    try:
        with opener.open(req, timeout=timeout) as resp:
            location = resp.headers.get("Location")
            if location:
                resolved = location
            else:
                body = resp.read(2048).decode("utf-8", errors="ignore")
                meta_match = re.search(r'URL=([^"\'>\s]+)', body, re.IGNORECASE)
                if meta_match:
                    resolved = meta_match.group(1)
    except HTTPError as err:
        location = err.headers.get("Location") if err.headers else None
        if location:
            resolved = location
    except (URLError, TimeoutError, ValueError):
        pass

    _TCO_CACHE[tco_url] = resolved
    return resolved


def enrich_tweet_links(raw_tweets: list[dict[str, Any]], resolve_tco: bool = True) -> list[dict[str, Any]]:
    """Menambahkan teks anchor dan hasil resolve shortlink t.co terpotong ke daftar href tweet."""
    if not resolve_tco:
        return raw_tweets

    for tweet in raw_tweets:
        anchors = tweet.get("anchors") or []
        tweet_text = tweet.get("teks", "")
        hrefs = list(tweet.get("hrefs") or [])

        for anchor in anchors:
            href = anchor.get("href", "")
            label = anchor.get("text", "")
            if label:
                hrefs.append(label)
            if href.startswith(("http://t.co/", "https://t.co/")) and should_resolve_tco(label, tweet_text):
                expanded = resolve_tco_url(href)
                if expanded and expanded != href:
                    hrefs.append(expanded)

        tweet["hrefs"] = hrefs
    return raw_tweets


# =========================
# 3. AUTENTIKASI & STORAGE STATE X
# =========================

def has_session_cookies(context: BrowserContext) -> bool:
    """Mengecek apakah cookie sesi aktif X (auth_token dan ct0) sudah ada."""
    try:
        cookies = context.cookies()
        names = {c.get("name"): c.get("value") for c in cookies if c.get("value")}
        return bool(names.get("auth_token") and names.get("ct0"))
    except Exception:
        return False


def is_logged_in(page: Page, context: BrowserContext) -> bool:
    """Memvalidasi apakah sesi X saat ini benar-benar masih aktif."""
    if not has_session_cookies(context):
        return False

    try:
        page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(1500)

        current_url = page.url.lower()
        if "/i/flow/login" in current_url or "/login" in current_url:
            return False

        account_btn = page.locator('[data-testid="SideNav_AccountSwitcher_Button"]')
        if account_btn.count() > 0 and account_btn.first.is_visible():
            return True

        sign_in_prompt = page.get_by_text("Sign in to X", exact=False)
        if sign_in_prompt.count() > 0 and sign_in_prompt.first.is_visible():
            return False

        return has_session_cookies(context)
    except Exception as exc:
        print(f"[WARN] Gagal memeriksa status login X: {exc}")
        return False


def login_x(
    page: Page,
    context: BrowserContext,
    username: str,
    password: str,
    state_path: Path,
    handle: str = "",
) -> None:
    """Login ke X menggunakan akun cadangan lalu menyimpan storage_state."""
    print("[INFO] Memulai proses login ke X (Twitter)...")
    page.goto(f"{BASE_URL}/i/flow/login", wait_until="domcontentloaded", timeout=30000)

    try:
        # Langkah 1: Isi username/email lalu lanjut ke langkah berikutnya
        user_input = page.locator('input[autocomplete="username"], input[name="text"]').first
        user_input.wait_for(state="visible", timeout=20000)
        user_input.fill(username)
        user_input.press("Enter")
        page.wait_for_timeout(1500)

        # Langkah 1.5: Tangani verifikasi tambahan bila X meminta handle/telepon sebelum password
        challenge_input = page.locator('input[data-testid="ocfEnterTextTextInput"]')
        if challenge_input.count() > 0 and challenge_input.first.is_visible():
            verify_value = handle.lstrip("@").strip() or username
            print("[INFO] X meminta verifikasi akun tambahan, mengisi handle/username...")
            challenge_input.first.fill(verify_value)
            challenge_input.first.press("Enter")
            page.wait_for_timeout(1500)

        # Langkah 2: Isi password lalu klik Log in
        pass_input = page.locator(
            'input[name="password"], input[autocomplete="current-password"], input[type="password"]'
        ).first
        pass_input.wait_for(state="visible", timeout=20000)
        pass_input.fill(password)

        login_btn = page.locator(
            '[data-testid="LoginForm_Login_Button"], button:text-is("Log in"), div[role="button"]:text-is("Log in")'
        ).first
        if login_btn.count() > 0 and login_btn.is_visible():
            login_btn.click()
        else:
            pass_input.press("Enter")

        print("[INFO] Menunggu verifikasi login X selesai (maks. 90 detik)...")
        deadline = time.time() + 90
        while time.time() < deadline:
            if has_session_cookies(context) and "/login" not in page.url.lower():
                break
            page.wait_for_timeout(1200)
        else:
            raise PlaywrightTimeoutError("Cookie sesi X (auth_token/ct0) tidak terbentuk dalam 90 detik.")

        page.wait_for_timeout(1500)
        context.storage_state(path=str(state_path))
        print(f"[INFO] Login X berhasil! Sesi disimpan ke {state_path}")
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "Timeout saat menunggu login X selesai. Pastikan kredensial di .env.credentials benar. "
            "Jika muncul CAPTCHA/verifikasi email, jalankan dengan HEADLESS=false dan selesaikan secara manual."
        ) from exc


# =========================
# 4. PARSING DOM & SCRAPING TWEET / BALASAN
# =========================

def extract_visible_tweets(page: Page) -> list[dict[str, Any]]:
    """Mengambil data mentah tweet/balasan yang sedang tampil di DOM X."""
    js_extract = """
    () => {
        const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
        const results = [];

        for (const el of articles) {
            const timeEl = el.querySelector('time');
            const userContainer = el.querySelector('div[data-testid="User-Name"]');
            let username = "";

            if (userContainer) {
                const links = Array.from(userContainer.querySelectorAll('a[href^="/"]'));
                for (const link of links) {
                    const txt = (link.innerText || "").trim();
                    if (txt.startsWith("@")) {
                        username = txt;
                        break;
                    }
                    const href = link.getAttribute("href") || "";
                    const m = href.match(/^\\/([A-Za-z0-9_]{1,30})$/);
                    if (m) {
                        username = `@${m[1]}`;
                        break;
                    }
                }
            }

            const tanggal = timeEl ? (timeEl.getAttribute("datetime") || timeEl.innerText || "").trim() : "";

            let postUrl = "";
            if (timeEl && timeEl.parentElement && timeEl.parentElement.tagName === "A") {
                postUrl = timeEl.parentElement.href || "";
            } else {
                const statusLink = el.querySelector('a[href*="/status/"]');
                postUrl = statusLink ? statusLink.href : "";
            }

            const tweetTextEl = el.querySelector('div[data-testid="tweetText"]');
            const teks = tweetTextEl ? (tweetTextEl.innerText || "").trim() : (el.innerText || "").trim();

            const anchorEls = Array.from((tweetTextEl || el).querySelectorAll('a[href]'));
            const anchors = anchorEls.map(a => ({
                href: a.href || a.getAttribute("href") || "",
                text: (a.getAttribute("title") || a.innerText || "").trim()
            }));
            const hrefs = anchors.map(a => a.href).filter(Boolean);

            if (teks || hrefs.length > 0) {
                results.push({
                    username,
                    tanggal,
                    teks,
                    hrefs,
                    anchors,
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
        print(f"[WARN] Gagal membaca DOM tweet: {exc}")
        return []


def collect_tweets_for_keyword(
    page: Page,
    keyword: str,
    search_mode: str,
    max_scrolls: int,
    delay_min: float,
    delay_max: float,
    resolve_tco: bool,
) -> tuple[list[dict[str, str]], list[tuple[str, str, str]], int]:
    """Mencari keyword di X, scroll hasil, lalu mengembalikan record dan kandidat balasan."""
    search_url = build_search_url(keyword, search_mode=search_mode)
    print(f"\n[INFO] Mencari keyword di X ({search_mode}): '{keyword}'")

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        wait_for_posts_ready(page, TWEET_SELECTOR)
    except Exception as exc:
        print(f"[ERROR] Gagal membuka pencarian '{keyword}': {exc}")
        return [], [], 0

    raw_tweets, scrolls_used = scroll_and_collect(
        page, extract_visible_tweets, max_scrolls, keyword, delay_min, delay_max
    )
    enrich_tweet_links(raw_tweets, resolve_tco=resolve_tco)
    records = posts_to_records(raw_tweets, keyword)

    candidate_info: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    for tweet in raw_tweets:
        url = tweet.get("post_url", "")
        norm = normalize_post_url(url)
        if url and norm not in seen_urls:
            seen_urls.add(norm)
            candidate_info.append((url, keyword, tweet.get("teks", "")))

    print(f"[INFO] Link ditemukan dari '{keyword}': {len(records)} | Kandidat tweet: {len(candidate_info)}")
    return records, candidate_info, scrolls_used


def collect_replies_for_tweet(
    page: Page,
    post_url: str,
    keyword: str,
    max_scrolls: int,
    delay_min: float,
    delay_max: float,
    resolve_tco: bool,
) -> tuple[list[dict[str, str]], int]:
    """Membuka halaman detail tweet untuk mengekstrak link dari balasan/komentar."""
    print(f"[INFO] Cek balasan tweet: {post_url}")
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=25000)
        wait_for_posts_ready(page, TWEET_SELECTOR)

        show_more = page.get_by_text("Show more replies", exact=False)
        if show_more.count() > 0 and show_more.first.is_visible():
            show_more.first.click()
            page.wait_for_timeout(1000)
    except Exception as exc:
        print(f"[WARN] Gagal membuka detail tweet {post_url}: {exc}")
        return [], 0

    raw_items, scrolls_used = scroll_and_collect(
        page, extract_visible_tweets, max_scrolls, "balasan", delay_min, delay_max
    )
    enrich_tweet_links(raw_items, resolve_tco=resolve_tco)
    records = posts_to_records(raw_items, keyword, parent_post_url=post_url)
    print(f"[INFO] Link ditemukan di balasan/tweet: {len(records)}")
    return records, scrolls_used


# =========================
# 5. FUNGSI UTAMA (ORKESTRASI)
# =========================

def main() -> None:
    """Menjalankan alur lengkap scraper X: muat env, cek/simpan sesi, scraping cepat, dan ekspor."""
    if sync_playwright is None:
        raise ImportError(
            "Library 'playwright' belum terpasang. Jalankan: pip install -r requirements.txt && playwright install chromium"
        )

    cred_file = load_environment()
    username = os.getenv("X_USERNAME", "").strip()
    password = os.getenv("X_PASSWORD", "").strip()
    handle = os.getenv("X_HANDLE", "").strip()
    keywords = parse_keywords(os.getenv("X_KEYWORDS"), default_keywords=DEFAULT_KEYWORDS)

    search_mode = os.getenv("X_SEARCH_MODE", "live").strip().lower()
    headless = os.getenv("HEADLESS", "false").strip().lower() == "true"
    block_media = os.getenv("BLOCK_MEDIA", "true").strip().lower() == "true"
    resolve_tco = os.getenv("RESOLVE_TRUNCATED_TCO", "true").strip().lower() == "true"
    scrape_comments = os.getenv("SCRAPE_COMMENTS", "true").strip().lower() == "true"

    delay_min = parse_float_env("SCROLL_DELAY_MIN", DEFAULT_SCROLL_DELAY_MIN, minimum=0.3)
    delay_max = max(delay_min, parse_float_env("SCROLL_DELAY_MAX", DEFAULT_SCROLL_DELAY_MAX, minimum=0.5))
    max_scrolls_per_session = parse_int_env("MAX_SCROLLS_PER_SESSION", DEFAULT_MAX_SCROLLS_PER_SESSION, minimum=1)
    max_scrolls_per_keyword = parse_int_env("MAX_SCROLLS_PER_KEYWORD", DEFAULT_MAX_SCROLLS_PER_KEYWORD, minimum=1)
    max_comment_posts = parse_int_env("MAX_COMMENT_POSTS_PER_SESSION", DEFAULT_MAX_COMMENT_POSTS, minimum=0)
    max_comment_scrolls = parse_int_env("MAX_COMMENT_SCROLLS_PER_POST", DEFAULT_MAX_COMMENT_SCROLLS, minimum=0)

    state_path = resolve_project_path(os.getenv("STORAGE_STATE_FILE", "storage_state.json"))
    output_csv = resolve_project_path(os.getenv("OUTPUT_CSV", "output/hasil_x_grup_wa.csv"))
    output_md = output_csv.with_suffix(".md")

    print(f"[INFO] Keyword aktif X ({len(keywords)}): {keywords}")
    print(
        f"[INFO] Mode: search={search_mode} | block_media={block_media} "
        f"| delay={delay_min:.1f}-{delay_max:.1f}s | batas_scroll={max_scrolls_per_session}"
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
                print("[INFO] Sesi login X masih aktif, melewati proses login.")
            else:
                if has_state_file:
                    print("[INFO] Sesi tersimpan X sudah kedaluwarsa, melakukan login ulang...")
                if not username or not password:
                    raise ValueError(
                        f"X_USERNAME dan X_PASSWORD wajib diisi di {cred_file} (atau .env) untuk login."
                    )
                login_x(page, context, username, password, state_path, handle=handle)

            if block_media:
                enable_fast_routing(context)
                print("[INFO] Blokir gambar/media/font aktif untuk mempercepat crawling.")

            # Tahap 1: Pencarian tiap keyword di X
            for keyword in keywords:
                remaining_scrolls = max_scrolls_per_session - total_scrolls
                if remaining_scrolls <= 0:
                    print(f"[INFO] Batas maksimal {max_scrolls_per_session} scroll per sesi tercapai.")
                    break

                keyword_scroll_budget = min(remaining_scrolls, max_scrolls_per_keyword)
                records, tweet_candidates, used = collect_tweets_for_keyword(
                    page, keyword, search_mode, keyword_scroll_budget, delay_min, delay_max, resolve_tco
                )
                all_records.extend(records)
                total_scrolls += used

                for url, kw, text in tweet_candidates:
                    norm = normalize_post_url(url)
                    if norm and norm not in seen_candidate_urls:
                        seen_candidate_urls.add(norm)
                        candidate_pool.append((url, kw, text))

                time.sleep(random.uniform(0.5, 1.0))

            # Tahap 2: Cek balasan pada tweet prioritas
            if scrape_comments and max_comment_posts > 0 and candidate_pool:
                tweets_to_check = prioritize_comment_candidates(candidate_pool)[:max_comment_posts]
                print(f"\n[INFO] Memeriksa balasan pada {len(tweets_to_check)} tweet prioritas...")

                for post_url, keyword in tweets_to_check:
                    remaining_scrolls = max_scrolls_per_session - total_scrolls
                    comment_scroll_budget = min(max_comment_scrolls, max(0, remaining_scrolls))

                    reply_records, used = collect_replies_for_tweet(
                        page, post_url, keyword, comment_scroll_budget, delay_min, delay_max, resolve_tco
                    )
                    all_records.extend(reply_records)
                    total_scrolls += used

                    time.sleep(random.uniform(0.5, 1.0))

        except KeyboardInterrupt:
            print("\n[WARN] Proses dihentikan pengguna. Menyimpan data yang sudah terkumpul...")
        except Exception as exc:
            print(f"[ERROR] Terjadi kesalahan selama scraping X: {exc}")
        finally:
            browser.close()

    filtered_records = apply_quality_filters_from_env(all_records, keywords)
    if filtered_records:
        save_to_csv(filtered_records, output_csv)
        save_to_markdown(filtered_records, output_md, platform_name="Twitter (X)")
        print_summary(filtered_records, output_csv, output_md)
    else:
        print("\n[SELESAI] Tidak ada link grup/referensi yang lolos filter pada sesi X ini.")


if __name__ == "__main__":
    main()
