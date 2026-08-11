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
    '"account director" OR PMO'
)

# Регион поиска: 113 = Россия, 1 = Москва, 2 = Санкт-Петербург (см. https://api.hh.ru/areas)
HH_AREA = os.environ.get('HH_AREA', '113')

# Как часто опрашивать HH.ru, секунд
HH_POLL_INTERVAL = int(os.environ.get('HH_POLL_INTERVAL', 1800))

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
