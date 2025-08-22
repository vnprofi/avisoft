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
import random

BASE_URL = "https://www.avito.ru"

# If running from PyInstaller bundle, point Playwright to embedded browsers
if getattr(sys, "_MEIPASS", None):
    embedded_dir = Path(sys._MEIPASS) / "ms-playwright"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(embedded_dir)

# ------------------ Anti-blocking helpers ------------------

# Rotating desktop User-Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def _get_random_headers() -> Dict[str, str]:
    return {
        "X-Forwarded-For": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
        "X-Real-IP": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
        "CF-Connecting-IP": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
    }


def _create_browser_context(playwright_instance, headless: bool = False):
    """Create Chromium browser/context/page with anti-detection settings."""
    browser = playwright_instance.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-web-security",
            "--disable-features=VizDisplayCompositor",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=random.choice(USER_AGENTS),
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        extra_http_headers=_get_random_headers(),
        java_script_enabled=True,
        bypass_csp=True,
        ignore_https_errors=True,
    )
    page = context.new_page()
    page.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en-US','en']});
        window.chrome = { runtime: {} };
        """
    )
    return browser, context, page


# ------------------ Parsing helpers ------------------


def _clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split()).replace("\xa0", " ")


def _clean_node_text(node) -> str:
    if not node:
        return ""
    for svg in node.find_all("svg"):
        svg.decompose()
    return " ".join(node.stripped_strings).replace("\xa0", " ").strip()


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
                browser, context, page = _create_browser_context(p, headless=headless)
            except Exception as e:
                print(f"Ошибка запуска браузера: {e}")
                raise PlaywrightError(f"Не удалось запустить браузер Chromium: {e}")

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

            # Раскрыть описания в списке (expand-text), если есть
            try:
                expand_links = page.query_selector_all('a[data-marker="expand-text"]')
                for link in expand_links:
                    try:
                        link.scroll_into_view_if_needed()
                        page.wait_for_timeout(300)
                        link.click()
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
            except Exception:
                pass

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


# ------------------ Details scraping ------------------


def extract_product_details(page, url: str) -> Dict:
    """Extract detailed info from a product card using an existing Playwright page."""
    try:
        # Randomize UA per request
        try:
            page.context.add_init_script(
                f"Object.defineProperty(navigator, 'userAgent', {{get: () => '{random.choice(USER_AGENTS)}'}});"
            )
        except Exception:
            pass

        page.goto(url, timeout=60000, wait_until="networkidle")
        page.wait_for_timeout(random.randint(3000, 6000))

        # Wait for basic readiness
        try:
            page.wait_for_selector("body", timeout=10000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_load_state("networkidle")
        except Exception:
            pass

        # Forced scrolling in several ways
        try:
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
            page.keyboard.press("End")
            page.wait_for_timeout(1000)
            page.evaluate("window.scrollBy(0, 3000)")
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # Раскрытие прайс-листа (улучшенная логика)
        try:
            price_list_buttons = page.query_selector_all('div._o8T3[data-marker*="PRICE_LIST_TITLE_MARKER"]')
            for button in price_list_buttons:
                try:
                    button.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    button.click()
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            if len(price_list_buttons) == 0:
                alt_buttons = page.query_selector_all('div.gVNL7 div._o8T3')
                for button in alt_buttons:
                    try:
                        button.click()
                        page.wait_for_timeout(1000)
                    except Exception:
                        pass
            page.evaluate("""
                const buttons = document.querySelectorAll('div[data-marker*="PRICE_LIST"], .gVNL7 ._o8T3, .gVNL7 .button');
                buttons.forEach(btn => { try { btn.click(); } catch(e) {} });
            """)
            page.wait_for_timeout(2000)
        except Exception:
            pass

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Location details
        location_detail = ""
        desc_blocks = soup.select('div.F3kIg')
        for block in desc_blocks:
            h2 = block.find('h2', class_='EEPdn')
            if h2 and 'Расположение' in h2.get_text():
                location_div = block.find('div', class_='ljYEJ')
                if location_div:
                    location_detail = _clean_node_text(location_div)
                break

        # Details
        details = ""
        details_block = soup.select_one('div.cK39j.gYppE.aoCbM div#bx_item-params')
        if details_block:
            details = _clean_node_text(details_block)

        # Price list (enhanced)
        price_list = extract_enhanced_price_list(soup)

        # Description
        description = ""
        desc_block = soup.select_one('div.cK39j.PqCav.aoCbM div#bx_item-description')
        if desc_block:
            description = _clean_node_text(desc_block)

        # Additional
        additional = ""
        edu_block = soup.select_one('div.UaGSK div[data-marker="service-education/title"]')
        if edu_block:
            parent_block = edu_block.find_parent('div', class_='UaGSK')
            if parent_block:
                additional = _clean_node_text(parent_block)

        return {
            "location_detail": location_detail,
            "details": details,
            "price_list": price_list,
            "description": description,
            "additional": additional,
        }
    except Exception as e:
        print(f"Ошибка при парсинге карточки {url}: {e}")
        return {
            "location_detail": "",
            "details": "",
            "price_list": "",
            "description": "",
            "additional": "",
        }


def extract_enhanced_price_list(soup):
    """УЛУЧШЕННОЕ извлечение прайс-листа (адаптировано под текущие helpers)."""
    price_list_parts = []

    price_block = soup.select_one('div.gVNL7')
    if not price_block:
        return ""

    # Заголовок
    title = price_block.select_one('h2.EEPdn')
    if title:
        price_list_parts.append(f"=== {_clean_node_text(title)} ===")

    # Элементы прайс-листа
    price_items = price_block.select('div[data-marker*="PRICE_LIST_VALUE_MARKER"]')
    for item in price_items:
        service_name = item.select_one('p.T7ujv.Tdsqf.G6wYF')
        name_text = _clean_node_text(service_name) if service_name else ""

        price_elem = item.select_one('strong.OVzrF')
        price_text = _clean_node_text(price_elem) if price_elem else ""

        additional_info = item.select_one('h1[data-marker="services-imv/title"]')
        info_text = _clean_node_text(additional_info) if additional_info else ""

        if name_text:
            result_line = f"• {name_text}"
            if price_text:
                result_line += f": {price_text}"
            if info_text and info_text != name_text:
                result_line += f" ({info_text})"
            price_list_parts.append(result_line)

    # Фоллбек — взять весь текст блока
    if len(price_list_parts) <= 1:
        all_text = _clean_node_text(price_block)
        if all_text and "Прайс-лист" in all_text:
            price_list_parts.append("Полный текст прайс-листа:")
            price_list_parts.append(all_text)

    return "\n".join(price_list_parts) if price_list_parts else ""


def collect_details_for_products(products: List[Dict]):
    """Augment each product dict with detailed fields by visiting item pages.
    Uses a single browser session for efficiency.
    """
    if not products:
        return
    with sync_playwright() as p:
        browser, context, page = _create_browser_context(p, headless=False)
        try:
            for product in products:
                details = extract_product_details(page, product.get("url", ""))
                product.update(details)
                time.sleep(random.uniform(3, 7))
        finally:
            browser.close()


# ------------------ CSV output ------------------


def save_to_csv(data: Dict, filename: str):
    """Save parsed data to CSV file. If detailed fields are present, include them."""
    if not data or not data.get("products"):
        raise ValueError("No product data to save")

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)

    products = data.get("products", [])
    has_details = any(
        any(k in row for k in ("location_detail", "details", "price_list", "description", "additional"))
        for row in products
    )

    if has_details:
        fieldnames = [
            "index",
            "name",
            "url",
            "title",
            "price",
            "location",
            "date",
            "location_detail",
            "details",
            "price_list",
            "description",
            "additional",
            "seller_name",
            "seller_rating",
        ]
    else:
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
        for row in products:
            row_out = dict(row)
            row_out.update({
                "seller_name": s.get("name", ""),
                "seller_rating": s.get("rating", ""),
            })
            writer.writerow(row_out)