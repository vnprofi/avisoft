import requests
from playwright.sync_api import sync_playwright, Error as PlaywrightError
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
import csv
import os
from typing import List, Dict
import subprocess
import sys
from pathlib import Path

BASE_URL = "https://www.avito.ru"

# If running from PyInstaller bundle, point Playwright to embedded browsers
if getattr(sys, "_MEIPASS", None):
    embedded_dir = Path(sys._MEIPASS) / "ms-playwright"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(embedded_dir)


def _clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split()).replace("\xa0", " ")


def _extract_price(card) -> str:
    price_span = card.select_one('meta[itemprop="price"]')
    if price_span and price_span.get("content"):
        return price_span["content"].strip()

    price_p = card.select_one('[data-marker="item-price"]')
    if price_p:
        return _clean_text(price_p.get_text(strip=True))
    return ""


def _extract_location(card) -> str:
    geo = card.select_one('div[class*="geo-root"]')
    if geo:
        return _clean_text(geo.get_text(strip=True))
    return ""


def _extract_date(card) -> str:
    date_p = card.select_one('[data-marker="item-date"]')
    if date_p:
        return _clean_text(date_p.get_text(strip=True))
    return ""


def _parse_listing_page(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    products = []
    item_cards = soup.select('[data-marker="item"], div[class*="iva-item-root"]')
    for idx, card in enumerate(item_cards, start=1):
        title_a = card.select_one('[data-marker="item-title"]')
        if not title_a:
            continue
        name = title_a.get_text(strip=True)
        href = title_a.get("href", "")
        url = urljoin(BASE_URL, href)
        products.append({
            "index": idx,
            "name": name,
            "url": url,
            "title": title_a.get("title", ""),
            "price": _extract_price(card),
            "location": _extract_location(card),
            "date": _extract_date(card),
        })

    # seller info (optional)
    seller_info = {}
    name_wrap = soup.find("div", class_=lambda x: x and "AvatarNameView-name" in x)
    if name_wrap:
        h = name_wrap.find(["h1", "h2"])
        if h:
            seller_info["name"] = h.get_text(strip=True)
    rating_span = soup.find("span", {"data-marker": "profile/score"})
    if rating_span:
        seller_info["rating"] = rating_span.get_text(strip=True)

    return {"products": products, "seller_info": seller_info, "html": soup}


def _get_next_page_url(current_url: str, page_number: int) -> str:
    # Avito uses ?p=2 parameter for pagination
    if "?" in current_url:
        return f"{current_url}&p={page_number}"
    return f"{current_url}?p={page_number}"


def fetch_products_for_seller(listing_url: str, max_pages: int = 10) -> Dict:
    """Парсит объявления продавца за один проход: открывает страницу,
    скроллит до конца и возвращает все найденные объявления.

    Параметр max_pages сохранён для обратной совместимости, но не используется.
    """
    try:
        html_text = _fetch_html_playwright(listing_url)
    except Exception as e:
        # Пробрасываем исключение, чтобы GUI показал ошибку
        raise

    parsed = _parse_listing_page(html_text)
    all_products: List[Dict] = parsed.get("products", [])
    seller_info: Dict = parsed.get("seller_info", {})

    return {
        "total_products": len(all_products),
        "products": all_products,
        "seller_info": seller_info,
    }


# ------------------ Playwright helper ------------------


def _fetch_html_playwright(url: str, scroll_pause: float = 0.5, max_scroll_attempts: int = 80, headless: bool = False) -> str:
    """Load page with Playwright, fast-scroll until all items rendered and return HTML.

    The function now relies on both the number of loaded items and the page height
    to detect a stable end-of-list, with a short grace period for pending loads.
    """
    _ensure_browsers_installed()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=headless)
            except Exception as e:
                print(f"Ошибка запуска браузера: {e}")
                raise PlaywrightError(f"Не удалось запустить браузер Chromium: {e}")

            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )
            page = context.new_page()

            # Avoid detection
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
            except Exception as e:
                print(f"Ошибка загрузки страницы: {e}")
                browser.close()
                raise PlaywrightError(f"Не удалось загрузить страницу: {e}")

            stable_cycles_required = 3
            stable_cycles = 0
            prev_items = -1
            prev_height = -1

            start_time = time.perf_counter()
            max_time_seconds = 90

            for _ in range(max_scroll_attempts):
                try:
                    # Scroll to bottom quickly
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(int(scroll_pause * 1000))

                    # Measure current state
                    current_items = len(page.query_selector_all('[data-marker="item"], div[class*="iva-item-root"]'))
                    current_height = page.evaluate("document.body.scrollHeight")

                    # Check stabilization
                    if current_items == prev_items and current_height == prev_height:
                        stable_cycles += 1

                        # Try clicking a generic "show more" button if present
                        try:
                            clicked_more = page.evaluate(
                                """
                                (() => {
                                  const buttons = Array.from(document.querySelectorAll('button, a'));
                                  const re = /(Показать\s*(ещё|еще|больше)|Show\s*more)/i;
                                  const el = buttons.find(b => re.test(b.textContent || ''));
                                  if (el) { el.click(); return true; }
                                  return false;
                                })()
                                """
                            )
                        except Exception:
                            clicked_more = False

                        if clicked_more:
                            # Give content a moment to load and continue
                            page.wait_for_timeout(800)
                            stable_cycles = 0
                            prev_items = -1
                            prev_height = -1
                            continue

                        if stable_cycles >= stable_cycles_required:
                            # Small grace period to let any pending data finish
                            try:
                                page.wait_for_load_state("networkidle", timeout=2000)
                            except Exception:
                                pass
                            # Final double-check
                            final_items = len(page.query_selector_all('[data-marker="item"], div[class*="iva-item-root"]'))
                            final_height = page.evaluate("document.body.scrollHeight")
                            if final_items == current_items and final_height == current_height:
                                break
                    else:
                        stable_cycles = 0

                    prev_items = current_items
                    prev_height = current_height

                    # Guard against excessive run time
                    if time.perf_counter() - start_time > max_time_seconds:
                        break
                except Exception as e:
                    print(f"Ошибка во время скроллинга: {e}")
                    break

            html = page.content()
            browser.close()
            return html

    except Exception as e:
        print(f"Критическая ошибка Playwright: {e}")
        raise PlaywrightError(f"Playwright не смог обработать страницу: {e}")


def _ensure_browsers_installed():
    """Check if Playwright browsers are installed; if not, install chromium."""
    # If embedded via PyInstaller and env var points to it – nothing to do
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        pth = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
        if pth.exists():
            return
    try:
        print("Installing Playwright chromium browsers, please wait…")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except Exception as e:
        print("Failed to install Playwright browsers:", e)


def save_to_csv(data: Dict, filename: str):
    """Save parsed data to CSV file"""
    if not data or not data.get("products"):
        raise ValueError("No product data to save")

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    fieldnames = [
        "index",
        "name",
        "url",
        "title",
        "price",
        "location",
        "date",
        "seller_name",
        "seller_rating",
    ]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        s = data.get("seller_info", {})
        for row in data["products"]:
            row_out = dict(row)
            row_out.update({
                "seller_name": s.get("name", ""),
                "seller_rating": s.get("rating", ""),
            })
            writer.writerow(row_out)