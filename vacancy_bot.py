"""
Telegram-бот для мониторинга каналов с вакансиями.
Слушает список каналов, фильтрует вакансии по ключевым словам/зарплате/чёрному списку
и пересылает подходящие в целевой канал.
"""
import logging
import asyncio
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from config import API_ID, API_HASH, PHONE, SOURCE_CHANNELS, TARGET_CHANNEL
from filters import is_vacancy_suitable

# ================= ЛОГИРОВАНИЕ =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("vacancy_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ================= TELEGRAM CLIENT =================
client = TelegramClient('user_session', API_ID, API_HASH)

@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    if not event.raw_text:
        return
    
    if not is_vacancy_suitable(event.raw_text):
        return
    
    logger.info("Найдена подходящая вакансия, пересылаю...")
    
    for attempt in range(3):
        try:
            await event.forward_to(TARGET_CHANNEL)
            logger.info("Вакансия успешно переслана")
            return
        except FloodWaitError as e:
            logger.warning(f"FloodWait: жду {e.seconds} сек (попытка {attempt + 1}/3)")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Ошибка пересылки: {e}")
            try:
                await client.send_message(TARGET_CHANNEL, event.raw_text)
                logger.info("Отправлено как обычное сообщение (fallback)")
            except Exception as e2:
                logger.error(f"Fallback тоже не сработал: {e2}")
            return

async def main():
    logger.info("Бот запущен и слушает каналы: %s", ", ".join(SOURCE_CHANNELS))
    await client.run_until_disconnected()

if __name__ == '__main__':
    client.start(phone=PHONE)
    client.loop.run_until_complete(main())
