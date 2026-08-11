"""
Общая логика фильтрации вакансий по ключевым словам/зарплате/чёрному списку.
Используется и vacancy_bot.py (Telegram-каналы), и hh_bot.py (HH.ru API).
"""
import re

# Ключевые слова для поиска (чем больше совпадений, тем лучше)
GOOD_KEYWORDS = [
    # Базовые PM-роли
    'project manager', 'руководитель проект', 'program manager', 'директор проект',
    'руководитель pmo', 'delivery manager', 'delivery', 'technical project manager',
    'tech project manager', 'руководитель направления', 'team lead', 'engineering manager',
    'account director', 'c-level', 'директор', 'руководитель',

    # Технологии и системы
    '1с', '1c', 'erp', 'управление холдингом', 'зуп', 'мсфо', 'рсбу', 'казначейств', 'бюджетирование',
    'финансов', 'банк', 'финтех', 'гис', 'гос', 'федеральн', 'дом.рф', 'гпб',
    'высоконагружен', 'интеграц', 'pmbok', 'waterfall', 'agile', 'jira', 'confluence',

    # Масштаб и процессы
    'бюджет от 100', 'бюджет от 200', 'команда от 30', 'команда от 50',
    'kpi', 'roadmap', 'stakeholder', 'стейкхолдер', 'портфель проект',
    'управлять проект', 'проектн', 'project', 'цифровизац', 'трансформаци',

    # Продукт и digital
    'продуктов', 'продуктовая', 'веб-сервис', 'веб-приложени', 'saas',
    'digital', 'digital-агентств', 'коммуникационн', 'тендер', 'клиентск'
]

# Чёрный список (если есть хоть одно слово - вакансия отбрасывается)
BLACKLIST = [
    # Низкий уровень
    'junior', 'джуниор', 'middle', 'миддл', 'стажёр', 'стажер', 'intern', 'без опыта',

    # Не те функции (заменено "маркетинг" на более специфичные)
    'продаж', 'sales manager',
    'smm', 'контент', 'копирайт', 'таргетолог', 'контекстолог',
    'маркетолог', 'специалист по маркетингу', 'digital маркетолог',
    'performance маркетолог', 'growth hacker', 'growth manager',

    # Дизайн и креатив
    'дизайнер', 'ux/ui', 'графическ', 'иллюстратор', 'арт-директор',

    # HR и рекрутинг
    r'\bhr\b', 'рекрут', 'подбор персонал', 'кадр', 'hrd',

    # Не те отрасли
    'horeca', 'ресторан', 'отел', 'гостиниц', 'кофеен',
    'строительств', 'девелопмент', 'жилой комплекс', r'\bжк\b',
    'сельск', 'агро', 'ферм', 'животноводств',
    'фарма', 'медицинск', 'клиник', 'больниц',
    'логистик', 'склад', 'фулфилмент', 'доставк', 'курьер',
    'gaming', 'игров', 'казино', 'беттинг', 'gambling',
    'fashion', 'beauty', 'косметик', 'одежд', 'обув',
    'e-commerce', 'ecom', 'маркетплейс', 'wildberries', 'ozon',

    # Разработчики и технические роли
    'frontend', 'front-end', 'фронтенд',
    'backend', 'back-end', 'бэкенд',
    'javascript', 'js ', 'react', 'vue', 'angular',
    'разработчик', 'developer', 'программист',
    'devops', 'sre', 'системн', 'администратор',
    'qa ', 'тестировщик', 'tester', 'qa engineer',
    'android', 'ios', 'mobile developer',
    'data scientist', 'ml engineer', 'data analyst',
    'python', 'java', 'php', 'ruby', 'go ', 'golang',
    'sql ', 'database', 'db ', 'базы данных',

    # Обучение и реклама
    'магистратур', 'грант', 'обучени', 'университет', 'образовани',
    'курс', 'стажировк', 'реклам', 'поступай', 'учеб', 'диплом',
    'программ', 'stem', 'онлайн-программ', 'офлайн программ',
    'вечерние занятия', 'выходным', 'государственный диплом',

    # Признаки резюме (а не вакансии)
    'рассматриваю позиции', 'ищу работу', 'резюме', 'cv', 'откликнусь',
    'готов к сотрудничеству', 'открыт к предложениям', 'могу начать',
    'опыт работы в', 'ключевые проекты', 'экспертиза:', 'контакты:',
    'telegram:', 'e-mail:', 'email:', 'телефон:',
    'linkedin', 'hh.ru', 'headhunter'
]

# Минимальная зарплата
MIN_SALARY = 250_000

# Слова, которые должны матчиться как отдельные слова
WORD_BOUNDARY_TERMS = {'1c', '1с', 'erp', 'hr', 'жк', 'js', 'go', 'db', 'qa'}

def _build_pattern(word: str) -> re.Pattern:
    """Строит regex для термина."""
    if word.startswith(r'\b'):
        return re.compile(word, re.IGNORECASE)
    if word.lower() in WORD_BOUNDARY_TERMS:
        return re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
    return re.compile(re.escape(word), re.IGNORECASE)

GOOD_PATTERNS = [_build_pattern(w) for w in GOOD_KEYWORDS]
BLACKLIST_PATTERNS = [_build_pattern(w) for w in BLACKLIST]

# Паттерн для поиска зарплаты
SALARY_PATTERN = re.compile(
    r'(\d{2,3}(?:[.,]\d{3})?)\s*(?:-\s*(\d{2,3}(?:[.,]\d{3})?)\s*)?'
    r'(?:тыс.?|тысяч|k|к|000|руб)',
    re.IGNORECASE,
)

def check_salary(text: str) -> bool:
    """Возвращает True, если зарплата не найдена либо >= MIN_SALARY."""
    matches = SALARY_PATTERN.findall(text)
    if not matches:
        return True
    amounts = []
    for first, second in matches:
        for raw in (first, second):
            if not raw:
                continue
            num = raw.replace(',', '.').replace(' ', '')
            try:
                value = float(num)
            except ValueError:
                continue
            amounts.append(value * 1000 if value < 1000 else value)
    if not amounts:
        return True
    return max(amounts) >= MIN_SALARY

def is_resume(text: str) -> bool:
    """Проверяет, является ли текст резюме (а не вакансией)."""
    text_lower = text.lower()
    resume_markers = [
        'рассматриваю позиции', 'ищу работу', 'мое резюме', 'моё резюме',
        'опыт работы в', 'ключевые проекты', 'экспертиза:',
        'контакты:', 'telegram:', 'e-mail:', 'email:', 'телефон:',
        'linkedin', 'hh.ru', 'headhunter', 'откликнусь',
        'готов к сотрудничеству', 'открыт к предложениям'
    ]
    return any(marker in text_lower for marker in resume_markers)

def is_vacancy_suitable(text: str) -> bool:
    """Проверяет, подходит ли вакансия под критерии."""
    if not text:
        return False

    # Сначала проверяем, не резюме ли это
    if is_resume(text):
        return False

    text_lower = text.lower()

    # Проверка чёрного списка
    for pattern in BLACKLIST_PATTERNS:
        if pattern.search(text):
            return False

    # Проверка зарплаты
    if not check_salary(text):
        return False

    # Подсчет совпадений с GOOD_KEYWORDS
    score = sum(1 for pattern in GOOD_PATTERNS if pattern.search(text))

    # Для C-level/директорских ролей снижаем порог до 1
    if any(term in text_lower for term in ['c-level', 'директор', 'account director', 'head of', 'chief']):
        return score >= 1

    # Для product manager нужен score >= 3 (чтобы отсеять чистых продактов без PM-опыта)
    if 'product manager' in text_lower or 'продакт' in text_lower:
        return score >= 3

    # Обычный порог
    return score >= 2
