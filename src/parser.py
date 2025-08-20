import requests
from playwright.sync_api import sync_playwright
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
import csv
import os
from typing import List, Dict

BASE_URL = "https://www.avito.ru"


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
    """Парсит объявления продавца, прокручивая страницу через Playwright."""
    all_products: List[Dict] = []
    seller_info: Dict = {}

    for page in range(1, max_pages + 1):
        page_url = listing_url if page == 1 else _get_next_page_url(listing_url, page)

        try:
            html_text = _fetch_html_playwright(page_url)
        except Exception:
            # Если Playwright не смог, прекращаем
            break

        parsed = _parse_listing_page(html_text)

        if page == 1:
            seller_info = parsed["seller_info"]

        products = parsed["products"]
        if not products:
            break

        all_products.extend(products)

        # Avito обычно показывает не более 50 объявлений на страницу.
        if len(products) < 50:
            break

    return {"total_products": len(all_products), "products": all_products, "seller_info": seller_info}


# ------------------ Playwright helper ------------------


def _fetch_html_playwright(url: str, scroll_pause: float = 0.5, max_scroll_attempts: int = 30) -> str:
    """Load page in Playwright, scroll to bottom to load items, return HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        page = context.new_page()
        # Avoid detection
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(2)

        scroll_attempts = 0
        last_items = 0

        while scroll_attempts < max_scroll_attempts:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(scroll_pause)
            new_items = len(page.query_selector_all('[data-marker="item"], div[class*="iva-item-root"]'))
            if new_items == last_items:
                scroll_attempts += 1
            else:
                scroll_attempts = 0
                last_items = new_items
            if scroll_attempts >= 3:
                break

        html = page.content()

        # Только скроллинг – без дополнительных кликов «Показать ещё»
        browser.close()
        return html


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