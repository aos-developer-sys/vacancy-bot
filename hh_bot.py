"""
Бот для поиска вакансий на HH.ru.
Использует официальный API (api.hh.ru) через OAuth client_credentials -
приложение зарегистрировано на dev.hh.ru, ключи в HH_CLIENT_ID/HH_CLIENT_SECRET.
Фильтрует по тем же критериям, что и vacancy_bot.py (общий filters.py),
и присылает новые подходящие вакансии в Telegram.
"""
import argparse
import asyncio
import datetime
import json
import logging
import re
import sqlite3
import time

import requests
from telethon import TelegramClient
from telethon.errors import FloodWaitError

from config import (
    API_ID, API_HASH, PHONE,
    HH_TARGET_CHANNEL, HH_SEARCH_TEXT, HH_AREA, HH_POLL_INTERVAL,
    HH_CLIENT_ID, HH_CLIENT_SECRET, HH_REQUEST_DELAY,
    HH_PROFESSIONAL_ROLES, HH_EXPERIENCE, HH_INITIAL_LOOKBACK_HOURS,
)
from filters import GOOD_PATTERNS, BLACKLIST_PATTERNS, MIN_SALARY

API_BASE = "https://api.hh.ru"
OAUTH_TOKEN_URL = "https://hh.ru/oauth/token"
USER_AGENT = "vacancy-bot/1.0 (personal use)"
DB_PATH = "hh_seen.sqlite3"
TOKEN_CACHE_PATH = "hh_token_cache.json"

PER_PAGE = 100
MAX_PAGES = 20  # HH сам ограничивает выдачу ~2000 результатами (per_page*page) - это запас
OVERLAP_MINUTES = 10  # нахлёст cutoff между опросами; дубликаты гасит таблица seen

TAG_RE = re.compile(r"<[^>]+>")

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

_TOKEN_CACHE = {"token": None, "expires_at": 0.0}


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, seen_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", html)).strip()


def _load_cached_token() -> tuple[str | None, float]:
    try:
        with open(TOKEN_CACHE_PATH) as f:
            data = json.load(f)
        return data.get("token"), data.get("expires_at", 0.0)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None, 0.0


def _save_cached_token(token: str, expires_at: float) -> None:
    with open(TOKEN_CACHE_PATH, "w") as f:
        json.dump({"token": token, "expires_at": expires_at}, f)


def get_access_token(force: bool = False) -> str:
    """OAuth client_credentials. Кэшируется в процессе И на диске (hh_token_cache.json) -
    hh_bot.py обычно запускается заново каждый цикл под systemd timer, а не живёт одним
    процессом, поэтому без дискового кэша каждый запуск запрашивал бы новый токен. HH же
    отклоняет повторный client_credentials-запрос 403 "app token refresh too early", пока
    предыдущий токен ещё не истёк (проверено живым запросом) - без кэша бот падал бы
    почти на каждом цикле. HH не всегда возвращает expires_in - если поля нет, считаем
    токен живым 1 час (консервативный дефолт), а не бессрочным."""
    now = time.time()
    if not force and _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"]:
        return _TOKEN_CACHE["token"]

    if not force:
        token, expires_at = _load_cached_token()
        if token and now < expires_at:
            _TOKEN_CACHE["token"], _TOKEN_CACHE["expires_at"] = token, expires_at
            return token

    resp = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": HH_CLIENT_ID,
            "client_secret": HH_CLIENT_SECRET,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    if resp.status_code == 403:
        # HH иногда отказывает в переоформлении, пока предыдущий токен ещё не истёк
        # ("app token refresh too early") - в этом случае используем то, что уже есть
        # (в памяти или на диске), вместо падения. Пробрасываем ошибку, только если
        # использовать реально нечего.
        fallback = _TOKEN_CACHE["token"] or _load_cached_token()[0]
        if fallback:
            logger.warning("HH отклонил обновление токена (%s), использую предыдущий", resp.text[:200])
            _TOKEN_CACHE["token"] = fallback
            _TOKEN_CACHE["expires_at"] = now + 300  # короткий запас, скоро попробуем обновить снова
            return fallback
    resp.raise_for_status()
    data = resp.json()
    expires_in = data.get("expires_in", 3600)
    token = data["access_token"]
    expires_at = now + expires_in - 60
    _TOKEN_CACHE["token"], _TOKEN_CACHE["expires_at"] = token, expires_at
    _save_cached_token(token, expires_at)
    logger.info("HH OAuth токен обновлён (истекает через %s сек)", expires_in)
    return token


def hh_api_get(path: str, params: dict) -> dict | None:
    """GET к api.hh.ru с Bearer-токеном. На 401 - один форс-рефреш токена и повтор.
    На 429/5xx - лог и None, чтобы вызывающий код мягко пропустил цикл/страницу."""
    for attempt in (1, 2):
        headers = {
            "Authorization": f"Bearer {get_access_token(force=(attempt == 2))}",
            "User-Agent": USER_AGENT,
        }
        resp = requests.get(f"{API_BASE}{path}", params=params, headers=headers, timeout=20)
        if resp.status_code == 401 and attempt == 1:
            logger.warning("HH API вернул 401, обновляю токен и повторяю запрос")
            continue
        if resp.status_code == 429:
            logger.warning("HH API вернул 429 (rate limit), пропускаю до следующего опроса")
            return None
        resp.raise_for_status()
        return resp.json()
    return None


def fetch_vacancies_since(date_from_iso: str) -> list[dict]:
    """Постранично забирает вакансии через официальный поиск HH.ru, отфильтрованные
    по тексту/региону/дате публикации на стороне HH - в отличие от RSS-экспорта,
    который всегда отдавал только топ-20 без пагинации."""
    params = {
        "text": HH_SEARCH_TEXT,
        "area": HH_AREA,
        "order_by": "publication_time",
        "search_field": "name",
        "date_from": date_from_iso,
        "per_page": PER_PAGE,
    }
    if HH_PROFESSIONAL_ROLES:
        params["professional_role"] = HH_PROFESSIONAL_ROLES.split(",")
    if HH_EXPERIENCE:
        params["experience"] = HH_EXPERIENCE.split(",")

    items = []
    for page in range(MAX_PAGES):
        data = hh_api_get("/vacancies", {**params, "page": page})
        if data is None:
            break
        items.extend(data.get("items", []))
        if page + 1 >= data.get("pages", 0):
            break
    return items


def fetch_full_description(vacancy_id: str) -> str | None:
    """Полное описание вакансии - список отдаёт только короткие snippet-выдержки,
    для скоринга по GOOD_KEYWORDS/BLACKLIST нужен полный текст."""
    data = hh_api_get(f"/vacancies/{vacancy_id}", {})
    if data is None:
        return None
    return strip_html(data.get("description", ""))


def extract_salary_rub(salary: dict | None) -> int | None:
    """Максимум из salary.from/to в рублях. Не-рублёвые зарплаты считаем
    неуказанными - как и раньше при regex-парсинге RSS-текста, который тоже
    распознавал только ₽. Без net/gross-пересчёта - берём число как есть."""
    if not salary or salary.get("currency") not in ("RUR", "RUB"):
        return None
    values = [v for v in (salary.get("from"), salary.get("to")) if v]
    return max(values) if values else None


def hh_vacancy_suitable(title: str, employer: str, description: str) -> bool:
    """Аналог filters.is_vacancy_suitable(), адаптированный под длинные официальные
    описания HH.ru: чёрный список сканируется только по вступлению, а не по всему
    тексту, и не применяется is_resume() (в выдаче поиска HH резюме не встречаются).
    Город здесь не проверяется - он уже отфильтрован на стороне HH через area=HH_AREA
    в fetch_vacancies_since()."""
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


def format_message(item: dict, salary: int | None) -> str:
    title = item.get("name", "")
    employer = (item.get("employer") or {}).get("name") or "—"
    region = (item.get("area") or {}).get("name") or "—"
    salary_str = f"от {salary:,} ₽".replace(",", " ") if salary else "не указана"
    url = item.get("alternate_url") or item.get("url", "")
    return f"💼 {title}\n🏢 {employer} · {region}\n💰 {salary_str}\n{url}"


async def send_new_vacancies(client: TelegramClient, conn: sqlite3.Connection, items: list, dry_run: bool) -> int:
    cur = conn.cursor()
    sent = 0
    for item in items:
        vid = str(item["id"])
        cur.execute("SELECT 1 FROM seen WHERE id = ?", (vid,))
        if cur.fetchone():
            continue

        salary = extract_salary_rub(item.get("salary"))
        salary_ok = salary is None or salary >= MIN_SALARY

        suitable = False
        if salary_ok:
            description = None
            try:
                description = fetch_full_description(vid)
            except Exception as e:
                logger.warning("Не удалось загрузить описание вакансии %s: %s", vid, e)
            time.sleep(HH_REQUEST_DELAY)
            suitable = hh_vacancy_suitable(
                item.get("name", ""),
                (item.get("employer") or {}).get("name", ""),
                description or "",
            )

        if not dry_run:
            cur.execute("INSERT OR IGNORE INTO seen (id, seen_at) VALUES (?, datetime('now'))", (vid,))
            conn.commit()

        if not suitable:
            continue

        message = format_message(item, salary)

        if dry_run:
            logger.info("[DRY-RUN] Подходит: %s", item.get("name", ""))
            sent += 1
            continue

        for attempt in range(3):
            try:
                await client.send_message(HH_TARGET_CHANNEL, message, link_preview=False)
                sent += 1
                logger.info("Отправлена вакансия: %s", item.get("name", ""))
                break
            except FloodWaitError as e:
                logger.warning("FloodWait: жду %s сек", e.seconds)
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error("Ошибка отправки: %s", e)
                break
    return sent


async def run_once(client: TelegramClient, conn: sqlite3.Connection, dry_run: bool = False) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = get_meta(conn, "last_cutoff")
    if not cutoff:
        cutoff = (now - datetime.timedelta(hours=HH_INITIAL_LOOKBACK_HOURS)).isoformat()

    try:
        items = fetch_vacancies_since(cutoff)
    except Exception as e:
        logger.error("Ошибка запроса к HH API: %s", e)
        return

    logger.info("Получено %d вакансий с HH.ru API", len(items))
    sent = await send_new_vacancies(client, conn, items, dry_run)
    logger.info("Разослано новых подходящих вакансий: %d", sent)

    if not dry_run:
        next_cutoff = now - datetime.timedelta(minutes=OVERLAP_MINUTES)
        set_meta(conn, "last_cutoff", next_cutoff.isoformat())


async def main(once: bool, dry_run: bool) -> None:
    conn = init_db()
    get_access_token()  # падаем сразу и явно, если ключи неверны, а не в глубине первого цикла
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
