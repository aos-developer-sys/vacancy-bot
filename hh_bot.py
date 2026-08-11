"""
Бот для поиска вакансий на HH.ru.
Официальный API для соискателей закрыт (discontinued 15.12.2025), поэтому используется
публичный RSS-экспорт поиска (hh.ru/search/vacancy/rss) + JSON-LD со страницы вакансии
для полного текста описания. Фильтрует по тем же критериям, что и vacancy_bot.py,
и присылает новые подходящие вакансии в Telegram.
"""
import argparse
import asyncio
import json
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET

import requests
from telethon import TelegramClient
from telethon.errors import FloodWaitError

from config import (
    API_ID, API_HASH, PHONE,
    HH_TARGET_CHANNEL, HH_SEARCH_TEXT, HH_AREA, HH_POLL_INTERVAL,
)
from filters import GOOD_PATTERNS, BLACKLIST_PATTERNS, MIN_SALARY

RSS_URL = "https://hh.ru/search/vacancy/rss"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DB_PATH = "hh_seen.sqlite3"
REQUEST_DELAY = 1.5  # пауза между запросами к страницам вакансий, чтобы не выглядеть как агрессивный скрапер

VACANCY_ID_RE = re.compile(r"/vacancy/(\d+)")
TAG_RE = re.compile(r"<[^>]+>")
RSS_SALARY_LINE_RE = re.compile(r"дохода:\s*(.+?)₽")
RSS_SALARY_NUM_RE = re.compile(r"\d{1,3}(?:\s\d{3})+|\d+")

# Для полного описания вакансии (2000+ символов) чёрный список сканируем только
# по вступлению - иначе случайные совпадения в тексте про плюшки/требования дают
# почти 100% ложных срабатываний (см. GOOD_KEYWORDS/BLACKLIST в filters.py, которые
# тюнились под короткие посты в Telegram-каналах, а не под полные официальные тексты).
BLACKLIST_SCAN_CHARS = 400

# ================= ЛОГИРОВАНИЕ =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("hh_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, seen_at TEXT)")
    conn.commit()
    return conn


def strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", html)).strip()


def parse_rss_salary(description: str) -> int | None:
    """Извлекает максимальное упомянутое значение зарплаты (в рублях) из текста RSS-описания."""
    m = RSS_SALARY_LINE_RE.search(description)
    if not m:
        return None
    values = [int(re.sub(r"\s", "", n)) for n in RSS_SALARY_NUM_RE.findall(m.group(1))]
    return max(values) if values else None


def hh_vacancy_suitable(title: str, employer: str, description: str) -> bool:
    """Аналог filters.is_vacancy_suitable(), адаптированный под длинные официальные
    описания HH.ru: чёрный список сканируется только по вступлению, а не по всему
    тексту, и не применяется is_resume() (в выдаче поиска HH резюме не встречаются)."""
    intro = " ".join([title, employer, description[:BLACKLIST_SCAN_CHARS]])
    if any(p.search(intro) for p in BLACKLIST_PATTERNS):
        return False

    full_text = " ".join([title, employer, description])
    text_lower = full_text.lower()
    score = sum(1 for p in GOOD_PATTERNS if p.search(full_text))

    if any(term in text_lower for term in ['c-level', 'директор', 'account director', 'head of', 'chief']):
        return score >= 1
    if 'product manager' in text_lower or 'продакт' in text_lower:
        return score >= 3
    return score >= 2


def fetch_rss_items() -> list:
    """Запрашивает свежие вакансии через публичный RSS-экспорт поиска hh.ru.

    RSS-эндпоинт всегда отдаёт только топ-20 свежайших результатов и игнорирует
    параметр page (проверено эмпирически), так что пагинации тут нет. При
    HH_POLL_INTERVAL порядка 30 минут и достаточно узком запросе 20 новых
    вакансий за интервал - разумный запас.
    """
    params = {
        "text": HH_SEARCH_TEXT,
        "area": HH_AREA,
        "order_by": "publication_time",
        "search_field": "name",
    }
    resp = requests.get(RSS_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    items = []
    for el in root.findall("./channel/item"):
        link = (el.findtext("link") or "").strip()
        match = VACANCY_ID_RE.search(link)
        if not match:
            continue
        items.append({
            "id": match.group(1),
            "url": link,
            "title": (el.findtext("title") or "").strip(),
            "rss_description": el.findtext("description") or "",
        })
    return items


def fetch_vacancy_details(url: str) -> dict | None:
    """Забирает JSON-LD со страницы вакансии: полное описание, работодателя, регион."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', resp.text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    location = ((data.get("jobLocation") or {}).get("address") or {})
    return {
        "title": data.get("title", ""),
        "description": strip_html(data.get("description", "")),
        "employer": (data.get("hiringOrganization") or {}).get("name", ""),
        "region": location.get("addressLocality", ""),
    }


def format_message(item: dict, details: dict | None, salary: int | None) -> str:
    title = (details or {}).get("title") or item["title"]
    employer = (details or {}).get("employer") or "—"
    region = (details or {}).get("region") or "—"
    salary_str = f"от {salary:,} ₽".replace(",", " ") if salary else "не указана"
    return f"💼 {title}\n🏢 {employer} · {region}\n💰 {salary_str}\n{item['url']}"


async def send_new_vacancies(client: TelegramClient, conn: sqlite3.Connection, items: list, dry_run: bool) -> int:
    cur = conn.cursor()
    sent = 0
    for item in items:
        vid = item["id"]
        cur.execute("SELECT 1 FROM seen WHERE id = ?", (vid,))
        if cur.fetchone():
            continue

        salary = parse_rss_salary(item["rss_description"])
        salary_ok = salary is None or salary >= MIN_SALARY

        details = None
        suitable = False
        if salary_ok:
            try:
                details = fetch_vacancy_details(item["url"])
            except Exception as e:
                logger.warning("Не удалось загрузить страницу вакансии %s: %s", item["url"], e)
            time.sleep(REQUEST_DELAY)
            suitable = hh_vacancy_suitable(
                item["title"],
                (details or {}).get("employer", ""),
                (details or {}).get("description", ""),
            )

        if not dry_run:
            cur.execute("INSERT OR IGNORE INTO seen (id, seen_at) VALUES (?, datetime('now'))", (vid,))
            conn.commit()

        if not suitable:
            continue

        message = format_message(item, details, salary)

        if dry_run:
            logger.info("[DRY-RUN] Подходит: %s", item["title"])
            sent += 1
            continue

        for attempt in range(3):
            try:
                await client.send_message(HH_TARGET_CHANNEL, message, link_preview=False)
                sent += 1
                logger.info("Отправлена вакансия: %s", item["title"])
                break
            except FloodWaitError as e:
                logger.warning("FloodWait: жду %s сек", e.seconds)
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error("Ошибка отправки: %s", e)
                break
    return sent


async def run_once(client: TelegramClient, conn: sqlite3.Connection, dry_run: bool = False) -> None:
    try:
        items = fetch_rss_items()
    except Exception as e:
        logger.error("Ошибка запроса к HH RSS: %s", e)
        return
    logger.info("Получено %d вакансий с HH.ru", len(items))
    sent = await send_new_vacancies(client, conn, items, dry_run)
    logger.info("Разослано новых подходящих вакансий: %d", sent)


async def main(once: bool, dry_run: bool) -> None:
    conn = init_db()
    client = TelegramClient("hh_session", API_ID, API_HASH)
    await client.start(phone=PHONE)
    logger.info("HH-бот запущен (area=%s, интервал=%d сек)", HH_AREA, HH_POLL_INTERVAL)

    if once:
        await run_once(client, conn, dry_run)
        await client.disconnect()
        return

    while True:
        await run_once(client, conn, dry_run)
        await asyncio.sleep(HH_POLL_INTERVAL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HH.ru vacancy bot")
    parser.add_argument("--once", action="store_true", help="Один проход вместо бесконечного цикла")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять сообщения и не писать в БД, только логировать совпадения")
    args = parser.parse_args()
    asyncio.run(main(once=args.once, dry_run=args.dry_run))
