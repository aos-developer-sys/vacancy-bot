import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']
PHONE = os.environ['PHONE']
TARGET_CHANNEL = os.environ['TARGET_CHANNEL']

# ================= HH.RU BOT =================
# Отдельный канал/чат для вакансий с HH.ru (по умолчанию - тот же, что и для каналов)
HH_TARGET_CHANNEL = os.environ.get('HH_TARGET_CHANNEL', TARGET_CHANNEL)

# Поисковый запрос HH.ru (синтаксис: https://hh.ru/article/1175 - поддерживает OR, кавычки)
HH_SEARCH_TEXT = os.environ.get(
    'HH_SEARCH_TEXT',
    '"project manager" OR "программный директор" OR "руководитель проекта" OR '
    '"руководитель проектов" OR "program manager" OR "delivery manager" OR '
    '"technical project manager" OR "engineering manager" OR "руководитель направления" OR '
    '"account director" OR PMO OR "проектный офис"'
)

# Регион поиска: 113 = Россия, 1 = Москва, 2 = Санкт-Петербург (см. https://api.hh.ru/areas)
HH_AREA = os.environ.get('HH_AREA', '113')

# Как часто опрашивать HH.ru, секунд
HH_POLL_INTERVAL = int(os.environ.get('HH_POLL_INTERVAL', 1800))

# OAuth-приложение HH.ru (dev.hh.ru), client_credentials flow. Обязательны.
HH_CLIENT_ID = os.environ['HH_CLIENT_ID']
HH_CLIENT_SECRET = os.environ['HH_CLIENT_SECRET']

# Пауза между запросами полного описания вакансии, секунд
HH_REQUEST_DELAY = float(os.environ.get('HH_REQUEST_DELAY', 0.3))

# Опционально: id профролей через запятую для сужения поиска на стороне HH
# (см. https://api.hh.ru/professional_roles). 107 = "Руководитель проектов".
# Пусто по умолчанию - таксономия HH слишком грубая, чтобы полагаться на неё одну;
# основной арбитр качества - keyword-скоринг в filters.py.
HH_PROFESSIONAL_ROLES = os.environ.get('HH_PROFESSIONAL_ROLES', '')

# Опционально: id уровней опыта через запятую (noExperience, between1And3,
# between3And6, moreThan6). Пусто = без фильтра по опыту на стороне HH.
HH_EXPERIENCE = os.environ.get('HH_EXPERIENCE', '')

# Глубина поиска в часах при самом первом запуске (пока нет сохранённого cutoff)
HH_INITIAL_LOOKBACK_HOURS = int(os.environ.get('HH_INITIAL_LOOKBACK_HOURS', 24))

SOURCE_CHANNELS = [
    'Relocats',
    'forproducts',
    'jobfortm',
    'forchiefs',
    'careerstation_pm',
    'habr_career',
    'zarubezhom_jobs',
    'vacanciesbest',
    'yojob',
    'young_relocate',
    'careerspace',
    'hcareers_jobs',
    'projects_jobs_feed',
    'workfortop',
    'agile_jobs',
    'workfortop_pro',
    't_crew',
    'mtsbankcareer',
    'wantapply_managers',
    'remote_jobs_relocate',
    'it_vakansii_jobs',
    'g_jobbot',
    'geekjobs',
    'remotegeekjob',
    'careerYlej',
]
