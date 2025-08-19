from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import csv
import re
import time

BASE_URL = "https://www.avito.ru"


def extract_seller_products(url):
    """
    Загружает страницу и возвращает распарсенные данные
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        page = context.new_page()

        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Очень быстрый скролинг для загрузки всех данных
            scroll_attempts = 0
            max_attempts = 50

            while scroll_attempts < max_attempts:
                current_items = len(page.query_selector_all('[data-marker="item"], div[class*="iva-item-root"]'))

                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(500)

                new_items = len(page.query_selector_all('[data-marker="item"], div[class*="iva-item-root"]'))

                if new_items == current_items:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0

                print(f"Загружено товаров: {new_items}")

                if scroll_attempts >= 3:
                    break

            # пробуем раскрыть описание (если есть кнопка)
            try:
                expand_links = page.query_selector_all('a[data-marker="expand-text"]')
                for link in expand_links:
                    try:
                        link.click()
                        page.wait_for_timeout(500)
                    except:
                        pass
            except:
                pass

            html = page.content()
        finally:
            browser.close()

    return extract_from_html(html)


def extract_from_html(html_content: str):
    """
    Парсит страницу и собирает товары + инфо о продавце
    """
    soup = BeautifulSoup(html_content, "html.parser")

    products = []
    item_cards = soup.select('[data-marker="item"], div[class*="iva-item-root"]')

    for i, card in enumerate(item_cards, start=1):
        title_a = card.select_one('a[data-marker="item-title"]')
        if not title_a:
            continue

        name = title_a.get_text(strip=True)
        href = title_a.get("href", "")
        url = urljoin(BASE_URL, href)

        price_value = extract_price(card)
        location_value = extract_location(card)
        date_value = extract_date(card)

        products.append(
            {
                "index": i,
                "name": name,
                "url": url,
                "title": title_a.get("title", ""),
                "price": price_value,
                "location": location_value,
                "date": date_value,
            }
        )

    seller_info = extract_seller_info(soup)

    return {
        "total_products": len(products),
        "products": products,
        "seller_info": seller_info,
    }


def clean_text(node) -> str:
    if not node:
        return ""
    for svg in node.find_all("svg"):
        svg.decompose()
    return " ".join(node.stripped_strings).replace("\xa0", " ").strip()


def extract_price(container) -> str:
    if not container:
        return ""

    meta_price = container.select_one('meta[itemprop="price"]')
    if meta_price and meta_price.get("content"):
        return meta_price["content"].strip()

    price_p = container.select_one('p[data-marker="item-price"]')
    if price_p:
        return clean_text(price_p)

    txt = clean_text(container)
    m = re.search(r"\d[\d\s]*₽", txt)
    if m:
        return m.group(0).strip()

    return ""


def extract_location(container) -> str:
    if not container:
        return ""

    geo = container.select_one('div[class*="geo-root"]')
    if geo:
        return clean_text(geo)

    return ""


def extract_date(container) -> str:
    """Извлекает дату размещения объявления"""
    if not container:
        return ""
    date_p = container.select_one('p[data-marker="item-date"]')
    if date_p:
        return clean_text(date_p)
    return ""


def extract_seller_info(soup):
    """
    Извлекает информацию о продавце
    """
    seller_info = {
        "name": "",
        "rating": "",
    }

    # Имя
    name_wrap = soup.find("div", class_=re.compile(r"AvatarNameView-name"))
    if name_wrap:
        h1 = name_wrap.find(["h1", "h2"])
        if h1:
            seller_info["name"] = h1.get_text(strip=True)

    # Рейтинг
    rating_el = soup.find("span", {"data-marker": "profile/score"})
    if rating_el:
        seller_info["rating"] = rating_el.get_text(strip=True)

    return seller_info


def save_to_csv(data, filename="avito_products.csv"):
    if not data.get("products"):
        print("Нет данных для сохранения")
        return

    with open(filename, "w", newline="", encoding="utf-8") as f:
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
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        s = data.get("seller_info", {}) or {}
        for row in data["products"]:
            row_out = dict(row)
            row_out.update(
                {
                    "seller_name": s.get("name", ""),
                    "seller_rating": s.get("rating", ""),
                }
            )
            writer.writerow(row_out)

    print(f"Данные сохранены в {filename}")


def main():
    url = "https://www.avito.ru/brands/8cbf4e5db1a75081a8fc4518d305c371/items?s=profile_search_show_all"

    print("Начинаем парсинг товаров…")
    result = extract_seller_products(url)

    print("\nИнформация о продавце:")
    for k, v in result.get("seller_info", {}).items():
        print(f"{k}: {v}")

    print(f"\nНайдено товаров: {result['total_products']}")
    for product in result["products"]:
        print(
            f"\n{product['index']}. {product['name']}\n"
            f"Цена: {product['price']}\n"
            f"Локация: {product['location']}\n"
            f"Дата: {product['date']}\n"
            f"URL: {product['url']}\n"
            f"Title: {product['title']}\n"
            + "-" * 50
        )

    save_to_csv(result)


if __name__ == "__main__":
    main()