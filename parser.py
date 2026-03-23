import asyncio
from playwright.async_api import async_playwright, TimeoutError
from playwright_stealth import Stealth
import random
import json
import re
import hashlib
import os
import aiohttp
from urllib.parse import urljoin
import psutil
import aiofiles
from typing import List

# ====================== НАСТРОЙКИ ======================
OUTPUT_FILE = "data/avito_ads.json"
PHOTOS_DIR = "data/photos"

STEP = 100_000
MAX_PRICE = 1_000_000_000
MAX_PAGES_PER_RANGE = 50
MAX_RETRIES_423 = 10
MAX_RETRIES_OTHER = 10
MAX_EMPTY_PAGE_RETRIES = 10

# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================
def kill_js_runtimes():
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if 'chrome' in proc.info['name'].lower():
                proc.terminate()
        except:
            pass


def extract_digits(s: str) -> str:
    return re.sub(r'\D', '', s or '')

def get_photo_filename(ad_url: str, index: int) -> str:
    hash_part = hashlib.md5(ad_url.encode()).hexdigest()[:12]
    return f"{hash_part}_{index:02d}.jpg"

async def download_photo(session, url, filepath):
    for _ in range(3):
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200: continue
                content = await resp.read()
                async with aiofiles.open(filepath, "wb") as f:
                    await f.write(content)
                return True
        except:
            await asyncio.sleep(1.5)
    return False

async def simulate_human_behavior(page):
    await page.evaluate("window.scrollBy(0, window.innerHeight * 0.6)")
    await page.wait_for_timeout(random.randint(800, 1800))
    await page.mouse.move(random.randint(100, 900), random.randint(100, 700))
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(random.randint(1200, 2500))

# ====================== ЗАПУСК БРАУЗЕРА ======================
async def launch_browser_context(headless=False):
    playwright = await async_playwright().start()
    stealth = Stealth()
    browser = await playwright.chromium.launch(
        headless=headless,
        slow_mo=0,
        args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-web-security",
                "--disable-features=VizDisplayCompositor"
        ]
    )
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        java_script_enabled=True,
        ignore_https_errors=True
    )
    page = await context.new_page()
    await stealth.apply_stealth_async(page)
    return browser, context, page, playwright

# ====================== ПРИМЕНЕНИЕ ФИЛЬТРА ЦЕНЫ ======================
async def apply_price_filter_and_get_url(page, min_price: int, max_price: int, max_retries: int = 10) -> tuple[bool, str | None]:
    """
    Применяет фильтр цены на Avito и возвращает (success, filtered_url).
    При ошибках 423/429 делает до max_retries попыток с экспоненциальной задержкой.
    """
    base_url = "https://www.avito.ru/all/kvartiry/prodam/vtorichka-ASgBAgICAkSSA8YQ5geMUg"

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[ФИЛЬТР ЦЕНЫ] попытка {attempt}/{max_retries} → {min_price:,} — {max_price:,} ₽")

            # Переходим на страницу выдачи
            response = await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            await simulate_human_behavior(page)

            # Обработка 423 / 429 / других блокировок
            if response.status in (423, 429, 503):
                sleep_time = min(2 ** attempt, 30) + random.uniform(0, 5)  # экспоненциальный backoff + шум
                print(f"[{response.status}] пауза {sleep_time:.1f} сек")
                await asyncio.sleep(sleep_time)
                continue

            if response.status == 302:
                print("[302] редирект → ждём загрузку страницы")

                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)

                    # пробуем найти объявления или пустую выдачу
                    await page.wait_for_selector(
                        'div[data-marker="item"], div[data-marker="search-empty"]',
                        timeout=10000
                    )

                    print("[302] страница загрузилась, продолжаем")
                except TimeoutError:
                    print("[302] после редиректа ничего не загрузилось → retry")
                    await asyncio.sleep(random.uniform(3, 8))
                    continue

            elif response.status in (423, 429, 503):
                sleep_time = min(2 ** attempt, 30) + random.uniform(0, 5)
                print(f"[{response.status}] пауза {sleep_time:.1f} сек")
                await asyncio.sleep(sleep_time)
                continue

            elif response.status != 200:
                print(f"[HTTP {response.status}] попытка {attempt}")
                await asyncio.sleep(random.uniform(4, 12))
                continue

            # Ждём загрузки блока фильтров
            try:
                await page.wait_for_selector('div[data-marker="search-filters"]', timeout=20000)
            except TimeoutError:
                print("[ФИЛЬТР] блок фильтров не найден → перезагрузка")
                continue

            # Заполняем "От"
            price_from_input = await page.query_selector('input[data-marker="price-from/input"]')
            if price_from_input:
                await price_from_input.fill(str(min_price))
                await asyncio.sleep(0.4)
            else:
                print("[WARN] Поле 'От' не найдено")

            # Заполняем "До"
            price_to_input = await page.query_selector('input[data-marker="price-to/input"]')
            if price_to_input:
                await price_to_input.fill(str(max_price))
                await asyncio.sleep(0.4)
            else:
                print("[WARN] Поле 'До' не найдено")

            # Кнопка "Применить"
            apply_button = await page.query_selector('button[data-marker="search-filters/submit-button"]')
            if apply_button:
                await apply_button.click(timeout=10000)
                await asyncio.sleep(random.uniform(4, 8))  # ждём перезагрузки выдачи
            else:
                print("[ERROR] Кнопка 'Применить' не найдена")
                continue

            # Проверяем, применился ли фильтр (ждём появления результатов или пустой выдачи)
            try:
                await page.wait_for_selector(
                    'div[data-marker="item"], div[data-marker="search-empty"]',
                    timeout=15000
                )
            except TimeoutError:
                print("[WARN] Результаты не загрузились после применения фильтра")
                continue

            filtered_url = page.url
            print(f"[УСПЕХ] Фильтр применён → URL: {filtered_url}")

            return True, filtered_url

        except TimeoutError as te:
            print(f"[TIMEOUT] попытка {attempt}: {te}")
            await asyncio.sleep(random.uniform(5, 15))
            continue

        except Exception as e:
            print(f"[ОШИБКА применения фильтра] попытка {attempt}: {e}")
            await asyncio.sleep(random.uniform(5, 15))
            continue

    print(f"[ФИЛЬТР ЦЕНЫ] все {max_retries} попыток провалились")
    return False, None

async def parse_ad_page(page, ad_url: str, max_retries: int = 8) -> dict:
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[объявление] {ad_url} попытка {attempt}/{max_retries}")

            response = await page.goto(ad_url, wait_until="domcontentloaded", timeout=60000)

            # Обработка ошибок 423/429 (блокировка/слишком много запросов)
            if response.status in (423, 429):
                sleep_time = random.uniform(3, 10) * (1.5 ** attempt)  # экспоненциальный backoff
                print(f"[{response.status}] пауза {sleep_time:.1f} сек (попытка {attempt})")
                await asyncio.sleep(sleep_time)
                if attempt == max_retries:
                    result["error"] = f"HTTP {response.status} после {max_retries} попыток"
                    return result
                continue

            if response.status != 200:
                print(f"[HTTP {response.status}] попытка {attempt}")
                if attempt < max_retries:
                    await asyncio.sleep(random.uniform(3, 8))
                    continue
                result["error"] = f"HTTP {response.status}"
                return result

            await simulate_human_behavior(page)

            price = "0"
            price_el = await page.query_selector('span[itemprop="price"], span[data-marker="price/value"], span[data-marker="price"]')
            if price_el:
                raw = await price_el.inner_text()
                price = extract_digits(raw)

            # Описание
            description = ""
            desc_el = await page.query_selector('div[data-marker="item-view/item-description"]')
            if desc_el:
                description = (await desc_el.inner_text()).strip()
            # 1. Координаты из карты
            latitude = None
            longitude = None
            try:
                map_wrapper = await page.query_selector('div[data-marker="item-map-wrapper"]')
                if map_wrapper:
                    lat_str = await map_wrapper.get_attribute("data-map-lat")
                    lon_str = await map_wrapper.get_attribute("data-map-lon")
                    
                    if lat_str:
                        try:
                            latitude = float(lat_str)
                        except:
                            pass
                            
                    if lon_str:
                        try:
                            longitude = float(lon_str)
                        except:
                            pass
                            
                    if latitude is not None and longitude is not None:
                        print(f"[координаты] {ad_url} → lat: {latitude}, lon: {longitude}")
            except Exception as map_err:
                print(f"[ошибка карты] {map_err}")

            # 2. Параметры (характеристики квартиры)
            params = {}
            try:
                # Основной контейнер
                params_block = await page.query_selector(
                    '[data-marker="item-view/item-params"], '
                    'div#bx_item-params ul, '
                    'ul.a428a905bbb93d32, '
                    'ul[class*="a428a"], '
                    'div[class*="params"] ul'
                )
                
                if params_block:
                    li_elements = await params_block.query_selector_all('li.d2936d013c910379, li[class*="d2936d"], li')
                    
                    for li in li_elements:
                        try:
                            full_text = await li.inner_text()
                            full_text = re.sub(r'\s+', ' ', full_text.replace('\xa0', ' ')).strip()
                            
                            if ':' not in full_text:
                                continue
                            
                            label_part, value_part = full_text.split(':', 1)
                            label = label_part.strip()
                            value = value_part.strip()
                            
                            # Если внутри есть ссылка (например, стоимость ремонта)
                            link_el = await li.query_selector('a, span a')
                            if link_el:
                                link_text = await link_el.inner_text()
                                link_text = re.sub(r'\s+', ' ', link_text.replace('\xa0', ' ')).strip()
                                if link_text:
                                    value = link_text
                            
                            if label and value:
                                params[label] = value
                                print(f"  → {label}: {value}")
                                
                        except:
                            continue
                else:
                    print("[параметры] блок не найден")
            except Exception as params_err:
                print(f"[ошибка параметров] {params_err}")


            photo_filenames = []
            try:
                # Основной контейнер с фотографиями
                photo_container = await page.query_selector(
                    'ul[data-marker="image-preview/preview-wrapper"].a8e4f10a535e061a'
                )
                
                if photo_container:
                    # Ищем только <li> с изображениями (исключаем элементы типа "mortgage-teaser")
                    img_items = await photo_container.query_selector_all(
                        'li[data-marker="image-preview/item"][data-type="image"]'
                    )
                    
                    print(f"[фото] найдено элементов с изображениями: {len(img_items)}")
                    
                    async with aiohttp.ClientSession() as session:
                        tasks = []
                        seen = set()

                        for idx, item in enumerate(img_items[:15], 1):
                            try:
                                # Получаем <img> внутри <li>
                                img = await item.query_selector('img')
                                if not img:
                                    continue

                                # Извлекаем URL из атрибутов
                                src = (
                                    await img.get_attribute('src') or
                                    await img.get_attribute('data-src') or
                                    await img.get_attribute('srcset')
                                )

                                if not src:
                                    continue

                                # Обработка srcset: берём последнее (самое большое) изображение
                                if 'srcset' in src and ',' in src:
                                    urls = [
                                        part.strip().split(' ')[0]
                                        for part in src.split(',')
                                        if part.strip()
                                    ]
                                    src = urls[-1] if urls else ""

                                # Очищаем URL
                                src = src.strip()
                                if not src.startswith('http'):
                                    src = urljoin('https://www.avito.ru', src)

                                # Фильтруем по расширениям
                                if src in seen:
                                    continue

                                # Генерируем имя файла
                                filename = get_photo_filename(ad_url, idx)
                                filepath = os.path.join(PHOTOS_DIR, filename)

                                # Проверяем, существует ли файл
                                if not os.path.exists(filepath):
                                    tasks.append(download_photo(session, src, filepath))

                                photo_filenames.append(filename)
                                seen.add(src)

                            except Exception as img_e:
                                print(f"[ошибка изображения {idx}]: {img_e}")

                        # Загружаем все изображения параллельно
                        if tasks:
                            results = await asyncio.gather(*tasks, return_exceptions=True)
                            success = sum(1 for r in results if r is True)
                            print(f"[фото] успешно скачано: {success}/{len(tasks)}")

                else:
                    print("[фото] контейнер с изображениями не найден")

            except Exception as photo_err:
                print(f"[общая ошибка парсинга фото]: {photo_err}")

            result = {
                "url": ad_url,
                "price": price,
                "description": description,
                "latitude": latitude,
                "longitude": longitude,
                **params,                    # все характеристики (площадь, этаж, ремонт и т.д.)
                "photos": photo_filenames,
            }
            
            print(f"[УСПЕХ] {ad_url}")
            return result

        except TimeoutError as te:
            print(f"[TIMEOUT] попытка {attempt}: {te}")
            if attempt < max_retries:
                await asyncio.sleep(random.uniform(4, 10))
                continue
            result["error"] = "Timeout после всех попыток"
            return result

        except Exception as e:
            print(f"[ОШИБКА] попытка {attempt}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(random.uniform(4, 10))
                continue
            result["error"] = str(e)
            return result

    return result

# Максимальное число параллельных браузеров/страниц
MAX_CONCURRENT_ADS = 3
semaphore = asyncio.Semaphore(MAX_CONCURRENT_ADS)

async def parse_ad_concurrent(link: str) -> None:
    async with semaphore:
        browser, context, page, playwright = None, None, None, None
        try:
            browser, context, page, playwright = await launch_browser_context()
            ad_data = await parse_ad_page(page, link)
            await append_to_json(ad_data)
        except Exception as e:
            print(f"[ОШИБКА] {link}: {e}")
        finally:
            if page: await page.close()
            if context: await context.close()
            if browser: await browser.close()
            if playwright: await playwright.stop()
            # kill_js_runtimes()
# ====================== ПАРСИНГ СТРАНИЦЫ ПОИСКА (С УЛУЧШЕННОЙ ОБРАБОТКОЙ ОШИБОК) ======================
async def parse_avito_page(page, page_num: int = 1, base_url: str = None, max_retries: int = 5) -> List[str]:
    """
    Парсит текущую страницу (если page_num=1) или переходит по URL с &p=N.
    Возвращает список ссылок на объявления.
    """
    links = []

    # Если это первая страница — используем уже открытую page (без goto)
    if page_num == 1:
        print(f"[СТРАНИЦА 1] парсим уже открытую страницу")
    else:
        if not base_url:
            raise ValueError("Для страниц > 1 нужен base_url с фильтром")
        full_url = f"{base_url}&p={page_num}"
        print(f"[СТРАНИЦА {page_num}] переход по {full_url}")

        for attempt in range(1, max_retries + 1):
            try:
                response = await page.goto(full_url, wait_until="domcontentloaded", timeout=60000)

                if response.status in (423, 429, 503):
                    if attempt < MAX_RETRIES_423:
                        sleep_time = random.uniform(1, 5) * (1.5 ** attempt)
                        print(f"[{response.status}] пауза {sleep_time:.1f} сек (попытка {attempt})")
                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        print(f"[{response.status}] исчерпаны попытки → пропускаем страницу")
                        return []

                if response.status != 200:
                    print(f"[HTTP {response.status}] попытка {attempt}")
                    await asyncio.sleep(random.uniform(3, 8))
                    continue
                
                break  # успешная загрузка

            except Exception as e:
                print(f"[ОШИБКА goto] попытка {attempt}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(random.uniform(4, 10))
                else:
                    return []
    await asyncio.sleep(random.uniform(3, 8))
    if page_num > 1 and page.url == base_url:
        print(f"[РЕДИРЕКТ НА ОРИГИНАЛ] {page.url} → диапазон исчерпан")
        return []
    
    # Общая обработка страницы (для 1-й и последующих)
    for attempt in range(1, MAX_RETRIES_OTHER + 1):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await simulate_human_behavior(page)

            # Проверяем наличие объявлений
            items = await page.query_selector_all('a[data-marker="item-title"]')
            if not items:
                if attempt < MAX_EMPTY_PAGE_RETRIES:
                    print(f"[ПУСТО] попытка {attempt}/{MAX_EMPTY_PAGE_RETRIES} → перезагрузка")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(random.uniform(3, 8))
                    continue
                else:
                    print(f"[ПУСТАЯ СТРАНИЦА] после {MAX_EMPTY_PAGE_RETRIES} попыток → конец диапазона")
                    return []

            for item in items:
                href = await item.get_attribute("href")
                if href:
                    full_link = urljoin("https://www.avito.ru", href)
                    links.append(full_link)

            print(f"[СТРАНИЦА {page_num}] найдено {len(links)} объявлений")
            return links

        except Exception as e:
            print(f"[ОШИБКА парсинга страницы] попытка {attempt}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(random.uniform(4, 10))
            else:
                return []

    print(f"[СТРАНИЦА {page_num}] все попытки провалились")
    return []


async def append_to_json(data: dict):
    async with aiofiles.open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False) + "\n")

# ====================== ОСНОВНОЙ ЦИКЛ ======================
async def main():
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    
    price_ranges = [(i, min(i + STEP - 1, MAX_PRICE)) for i in range(0, MAX_PRICE + 1, STEP)]

    print(f"Всего диапазонов: {len(price_ranges)}")

    for idx, (min_p, max_p) in enumerate(price_ranges, 1):
        print(f"\n{'═' * 70}")
        print(f"ДИАПАЗОН {idx}/{len(price_ranges)} → {min_p:,} — {max_p:,} ₽")
        print(f"{'═' * 70}\n")

        browser, context, page, playwright = await launch_browser_context(headless=False)
        
        filtered_url = None  # будет заполнен после применения фильтра
        
        try:
            # 1. Применяем фильтр на первой странице
            success, filtered_url = await apply_price_filter_and_get_url(page, min_p, max_p)
            
            if not success or not filtered_url:
                print("Не удалось применить фильтр или получить URL → пропускаем диапазон")
                await asyncio.sleep(random.uniform(60, 180))
                continue

            print(f"Фильтр применён. Базовый URL для всех страниц: {filtered_url}")

            page_num = 1
            while page_num <= MAX_PAGES_PER_RANGE:
                links = await parse_avito_page(page, page_num=page_num, base_url=filtered_url)

                if not links:
                    print(f"Диапазон исчерпан на странице {page_num}")
                    break

                tasks = [parse_ad_concurrent(link) for link in links]
                await asyncio.gather(*tasks, return_exceptions=True)

                page_num += 1
                await asyncio.sleep(random.uniform(8, 18))

        except Exception as e:
            print(f"[Критическая ошибка в диапазоне {min_p:,}–{max_p:,}]: {e}")
        finally:
            if page: await page.close()
            if context: await context.close()
            if browser: await browser.close()
            if playwright: await playwright.stop()

        # Большая пауза между диапазонами
        await asyncio.sleep(random.uniform(90, 240))

    print("\nПАРСИНГ ПО ВСЕМ ДИАПАЗОНАМ ЗАВЕРШЁН!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем")
        kill_js_runtimes()