import re
import json
from datetime import datetime
from bs4 import BeautifulSoup


def _parse_date_str(date_str: str) -> datetime | None:
    """Пытается распарсить строку даты в различных форматах."""
    date_str = date_str.strip().replace('\xa0', ' ')
    # Убираем суффиксы часовых поясов: ", по МСК", "(МСК)", "МСК" и т.д.
    date_str = re.sub(r',?\s*(?:по\s+)?(?:МСК|MSK|мск)\s*$', '', date_str, flags=re.IGNORECASE)
    date_str = re.sub(r'\s*\((?:МСК|MSK|мск)\)\s*$', '', date_str, flags=re.IGNORECASE)
    # Убираем множественные пробелы
    date_str = re.sub(r'\s+', ' ', date_str).strip()
    
    formats = [
        '%d.%m.%Y %H:%M:%S',  # 15.04.2026 12:00:00
        '%d.%m.%Y %H:%M',     # 15.04.2026 12:00
        '%d.%m.%Y',           # 15.04.2026
        '%Y-%m-%dT%H:%M:%S',  # 2026-04-15T12:00:00 (ISO)
        '%Y-%m-%d %H:%M:%S',  # 2026-04-15 12:00:00
        '%Y-%m-%d %H:%M',     # 2026-04-15 12:00
        '%Y-%m-%d',           # 2026-04-15
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


# Единый набор паттернов для извлечения дедлайна из текста
# Паттерны расположены от самых специфичных к самым общим
# Суффикс часового пояса — опциональная часть паттерна (например ", по МСК")
_TZ_SUFFIX = r'(?:[,.]?\s*(?:по\s+)?(?:МСК|MSK))?'
# Дата+время с опциональными секундами (формат DD.MM.YYYY)
_DT = r'(\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?)' + _TZ_SUFFIX
# Только дата (формат DD.MM.YYYY)
_D = r'(\d{1,2}\.\d{1,2}\.\d{4})' + _TZ_SUFFIX

# ISO формат дата+время (YYYY-MM-DD HH:MM или YYYY-MM-DDTHH:MM)
_ISO_DT = r'(\d{4}-\d{2}-\d{2}[T\s]\d{1,2}:\d{2}(?::\d{2})?)'
# ISO формат только дата
_ISO_D = r'(\d{4}-\d{2}-\d{2})'

_DEADLINE_TEXT_PATTERNS = [
    # --- ISO формат с дата+время ---
    re.compile(r'Дата\s+окончания\s+подачи\s+(?:заявок|предложений)\s*[:\s]*' + _ISO_DT, re.IGNORECASE),
    re.compile(r'Актуально\s+до\s*[:\s]*' + _ISO_DT, re.IGNORECASE),
    re.compile(r'до\s+' + _ISO_DT, re.IGNORECASE),
    re.compile(r'Окончание\s*[:\s]*' + _ISO_DT, re.IGNORECASE),

    # --- Специфичные паттерны (с меткой + дата+время+секунды, формат DD.MM.YYYY) ---
    re.compile(r'Дата\s+окончания\s+подачи\s+(?:заявок|предложений)\s*[:\s]*' + _DT, re.IGNORECASE),
    re.compile(r'Окончани[ея]\s+при[её]ма\s+(?:заявок|предложений)\s*[:\s]*' + _DT, re.IGNORECASE),
    re.compile(r'Срок\s+(?:подачи|окончания\s+подачи)\s+(?:заявок|предложений)\s*[:\s]*' + _DT, re.IGNORECASE),
    re.compile(r'При[её]м\s+(?:заявок|предложений)\s+до\s*[:\s]*' + _DT, re.IGNORECASE),
    re.compile(r'Подача\s+(?:заявок|предложений)\s+до\s*[:\s]*' + _DT, re.IGNORECASE),
    re.compile(r'(?:Заявки|Предложения)\s+(?:принимаются\s+)?до\s*[:\s]*' + _DT, re.IGNORECASE),

    # --- Менее специфичные (Актуально до, Окончание:) + дата+время ---
    re.compile(r'Актуально\s+до\s*[:\s]*' + _DT, re.IGNORECASE),
    re.compile(r'Окончание\s*[:\s]*' + _DT, re.IGNORECASE),

    # --- "до" + дата+время (может быть слишком широким, но ставим после специфичных) ---
    re.compile(r'до\s+' + _DT, re.IGNORECASE),

    # --- Только дата (без времени) DD.MM.YYYY — самые низкоприоритетные ---
    re.compile(r'Дата\s+окончания\s+подачи\s+(?:заявок|предложений)\s*[:\s]*' + _D, re.IGNORECASE),
    re.compile(r'Окончани[ея]\s+при[её]ма\s+(?:заявок|предложений)\s*[:\s]*' + _D, re.IGNORECASE),
    re.compile(r'Срок\s+(?:подачи|окончания\s+подачи)\s+(?:заявок|предложений)\s*[:\s]*' + _D, re.IGNORECASE),
    re.compile(r'Актуально\s+до\s*[:\s]*' + _D, re.IGNORECASE),
    re.compile(r'до\s+' + _D, re.IGNORECASE),
]


def _search_deadline_in_text(text: str) -> datetime | None:
    """Ищет дедлайн в нормализованном тексте страницы."""
    for pattern in _DEADLINE_TEXT_PATTERNS:
        match = pattern.search(text)
        if match:
            result = _parse_date_str(match.group(1))
            if result:
                return result
    return None


def _search_deadline_in_html_structure(soup: BeautifulSoup, html_content: str) -> datetime | None:
    """
    Структурный поиск дедлайна по HTML:
    1. Ищет элементы-метки ('Дата окончания', 'Окончание приёма' и т.д.)
       и берёт дату из соседнего элемента (td, span, div)
    2. Ищет по ID/классам контейнеров
    3. Ищет в raw HTML через regex
    """
    # Набор ключевых фраз для поиска метки дедлайна
    deadline_labels = [
        'дата окончания подачи заявок',
        'дата окончания подачи предложений',
        'дата окончания подачи',
        'дата окончания приема заявок',
        'дата окончания приема предложений',
        'дата окончания приёма заявок',
        'дата окончания приёма предложений',
        'окончание подачи заявок',
        'окончание подачи предложений',
        'окончание приема заявок',
        'окончание приема предложений',
        'окончание приёма заявок',
        'окончание приёма предложений',
        'срок подачи заявок',
        'срок подачи предложений',
        'срок окончания подачи',
        'приём заявок до',
        'приём предложений до',
        'прием заявок до',
        'прием предложений до',
        'приём заявок',
        'приём предложений',
        'прием заявок',
        'прием предложений',
        'подача заявок до',
        'подача предложений до',
        'заявки принимаются до',
        'предложения принимаются до',
        'актуально до',
        'до ',
    ]
    
    # 1. Структурный поиск: ищем элемент с текстом-меткой, затем берём дату из соседнего
    for label_text in deadline_labels:
        label_el = soup.find(lambda t: t.string and label_text in t.get_text(strip=True).lower())
        if not label_el:
            # Пробуем поиск по частичному совпадению текста (не только .string)
            label_el = soup.find(lambda t: t.name in ['td', 'th', 'span', 'div', 'dt', 'label', 'p'] 
                                 and label_text in t.get_text(strip=True).lower()
                                 and len(t.get_text(strip=True)) < 100)
        if label_el:
            # Ищем дату в следующем элементе-соседе
            sibling = label_el.find_next_sibling()
            if sibling:
                sibling_text = sibling.get_text(strip=True).replace('\xa0', ' ')
                date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4}\s*\d{1,2}:\d{2}(?::\d{2})?)', sibling_text)
                if not date_match:
                    date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', sibling_text)
                if date_match:
                    result = _parse_date_str(date_match.group(1))
                    if result:
                        return result
            
            # Fallback: find_next() — для SPA где label и value не являются прямыми siblings
            next_el = label_el.find_next(['div', 'span', 'td', 'dd', 'p'])
            if next_el and next_el != label_el:
                next_text = next_el.get_text(strip=True).replace('\xa0', ' ')
                date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4}\s*\d{1,2}:\d{2}(?::\d{2})?)', next_text)
                if not date_match:
                    date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', next_text)
                if date_match:
                    result = _parse_date_str(date_match.group(1))
                    if result:
                        return result
            
            # Ищем дату прямо в тексте элемента (может быть "Приём заявок до 15.04.2026 12:00")
            el_text = label_el.get_text(strip=True).replace('\xa0', ' ')
            date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4}\s*\d{1,2}:\d{2}(?::\d{2})?)', el_text)
            if not date_match:
                date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', el_text)
            if date_match:
                result = _parse_date_str(date_match.group(1))
                if result:
                    return result

            # Ищем в родительском элементе (может быть в <tr>)
            parent = label_el.parent
            if parent:
                parent_text = parent.get_text(separator=' ', strip=True).replace('\xa0', ' ')
                date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?)', parent_text)
                if not date_match:
                    date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', parent_text)
                if date_match:
                    result = _parse_date_str(date_match.group(1))
                    if result:
                        return result

    # 2. Поиск по ID/классам контейнеров и новым data-xid атрибутам
    date_containers = soup.find_all(attrs={'id': re.compile(r'date_end|deadline|end_date', re.IGNORECASE)})
    if not date_containers:
        date_containers = soup.find_all(attrs={'class': re.compile(r'date-end|deadline|end-date', re.IGNORECASE)})
    if not date_containers:
        date_containers = soup.find_all(attrs={'data-xid': 'consider-from'})
    if not date_containers:
        date_containers = soup.find_all(attrs={'data-xid': 'remains-days'})
    
    for container in date_containers:
        text = container.get_text(strip=True).replace('\xa0', ' ')
        date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4}\s*\d{1,2}:\d{2}(?::\d{2})?)', text)
        if not date_match:
            date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', text)
        if date_match:
            result = _parse_date_str(date_match.group(1))
            if result:
                return result

    # 3. Последняя попытка: regex по raw HTML
    html_patterns = [
        # <td>15.04.2026 12:00</td> после "Дата окончания подачи заявок"
        re.compile(r'(?:Дата\s+окончания|Окончани[ея]\s+при[её]ма).*?<(?:td|span|div)[^>]*>\s*(\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*</(?:td|span|div)>', re.IGNORECASE | re.DOTALL),
        # <span id="date_end_date_container">15.04.2026</span> в <span id="date_end_time_container">12:00</span>
        re.compile(r'date_end_date_container[^>]*>\s*(\d{1,2}\.\d{1,2}\.\d{4})\s*</span>.*?date_end_time_container[^>]*>\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*</span>', re.IGNORECASE | re.DOTALL),
        # data-атрибуты с ISO-датой
        re.compile(r'data-(?:end|deadline|date-end)[^=]*=\s*["\'](\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)["\']', re.IGNORECASE),
        # JSON-подобные данные (на случай новых SPA)
        re.compile(r'["\']date_end_applications["\']\s*:\s*["\']([^"\']+)["\']', re.IGNORECASE),
        re.compile(r'["\']dateEndApplications["\']\s*:\s*["\']([^"\']+)["\']', re.IGNORECASE),
    ]
    
    for pattern in html_patterns:
        match = pattern.search(html_content)
        if match:
            if len(match.groups()) == 2:
                date_str = f"{match.group(1)} {match.group(2)}"
            else:
                date_str = match.group(1)
            result = _parse_date_str(date_str)
            if result:
                return result

    return None


def extract_b2b_data(html_content: str, current_url: str = None) -> dict | None:
    """
    Парсит HTML-страницу тендера с B2B-Center и извлекает ключевые данные.
    Возвращает dict с полями: id, title, nmcc, participants_count, url.
    Возвращает None, если не удалось извлечь данные.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    if "проверка браузера" in html_content.lower() or "servicepipe" in html_content.lower():
        # Если это страница-заглушка проверки, не считаем ее полезной
        return None

    # Early-check: скрытые участники ("Всего заявок: Скрыто" / "Заявки · скрыто").
    # Проверяем до всех ветвлений, т.к. некоторые ветки возвращают participants_count=0
    # не проверив этот флаг (старые тендеры с <h1> попадают в блок "нового дизайна").
    _hidden_pattern = re.compile(
        r'(?:Поступивши[ех]\s+заявк[иа]|Всего\s+заявок|Заявки)\s*[—–\-:·•\s]+\s*(скрыт|недоступ)',
        re.IGNORECASE
    )
    _participants_hidden = bool(_hidden_pattern.search(soup.get_text(separator=' ').replace('\xa0', ' ')))

    # НОВЫЙ ПОДХОД: ищем данные внедренные parser.py

    # 1. Сначала проверяем есть ли специальный текст от SPA-страницы
    spa_text_script = soup.find('script', {'id': '__SPA_RENDERED_TEXT__'})
    if spa_text_script and spa_text_script.string:
        try:
            spa_data = json.loads(spa_text_script.string)
            if spa_data and spa_data.get('text'):
                # Используем этот текст для поиска дедлайна
                page_text = spa_data['text'].replace('\xa0', ' ')
                page_text = re.sub(r'\s+', ' ', page_text)
                deadline_dt = _search_deadline_in_text(page_text)

                # Если дедлайн не найден в SPA тексте, пробуем структурный поиск
                if not deadline_dt:
                    deadline_dt = _search_deadline_in_html_structure(soup, html_content)

                # Для SPA страниц дедлайн - самое важное, так что если нашли - хорошо
                if deadline_dt:
                    # Извлекаем ID и название из основной части HTML
                    tender_id = ""
                    if current_url:
                        tid_match = re.search(r'tender-(\d+)', current_url) or re.search(r'id=(\d+)', current_url)
                        if tid_match:
                            tender_id = tid_match.group(1)

                    title = ""
                    title_el = soup.find('h1')
                    if title_el:
                        title = title_el.get_text(strip=True)

                    if tender_id or title:
                        return {
                            "id": tender_id,
                            "title": title or 'Без названия',
                            "nmcc": 0.0,
                            "participants_count": -1 if _participants_hidden else 0,
                            "deadline": deadline_dt.isoformat(),
                            "url": current_url
                        }
        except (json.JSONDecodeError, ValueError) as e:
            # Продолжаем с обычным парсингом
            pass

    # 2. Проверяем старый вариант с полными данными
    tender_data_script = soup.find('script', {'id': '__B2B_TENDER_DATA__'})
    if tender_data_script and tender_data_script.string:
        try:
            tender_data = json.loads(tender_data_script.string)
            if tender_data and tender_data.get('data'):
                data = tender_data['data']
                # Если есть хотя бы ID или название, пытаемся использовать эти данные
                if data.get('id') or data.get('name'):
                    # Ищем дедлайн если его нет в data
                    deadline = data.get('deadline')
                    if not deadline:
                        # Ищем в тексте страницы
                        page_text = soup.get_text(separator=' ').replace('\xa0', ' ')
                        page_text = re.sub(r'\s+', ' ', page_text)
                        deadline_dt = _search_deadline_in_text(page_text)
                        if not deadline_dt:
                            deadline_dt = _search_deadline_in_html_structure(soup, html_content)
                        deadline = deadline_dt.isoformat() if deadline_dt else None

                    _pc = int(data.get('offers_count', 0) or 0)
                    if _pc == 0 and _participants_hidden:
                        _pc = -1
                    return {
                        "id": str(data.get('id', '')),
                        "title": data.get('name', 'Без названия'),
                        "nmcc": float(data.get('start_price', 0) or 0),
                        "participants_count": _pc,
                        "deadline": deadline,
                        "url": current_url
                    }
        except (json.JSONDecodeError, ValueError) as e:
            # Если парсинг не удался, продолжаем с обычным парсингом
            pass
    
    # --- НОВЫЙ ДИЗАЙН (market-next / SPA) ---

    # 1. Пробуем найти данные в JSON внутри <script> тегов (Pinia / Nuxt)
    for script in soup.find_all('script'):
        script_text = script.string
        if not script_text: continue
        
        # Новый формат - Pinia
        if 'window.__pinia' in script_text:
            try:
                # Извлекаем объект Pinia
                # Он может быть как JSON.parse так и просто объектом
                json_match = re.search(r'window\.__pinia\s*=\s*(?:JSON\.parse\()?(["\'{].+?)(?:\))?;?\s*$', script_text, re.MULTILINE | re.DOTALL)
                if json_match:
                    pinia_str = json_match.group(1).strip()
                    # Если строка в одинарных кавычках (результат JSON.parse)
                    if pinia_str.startswith("'") and pinia_str.endswith("'"):
                        pinia_str = pinia_str[1:-1].encode('utf-8').decode('unicode_escape')
                    
                    data = json.loads(pinia_str)
                    # В Pinia данные тендера обычно в сторах 'tender' или 'market'
                    for store_name in ['tender', 'market']:
                        store = data.get(store_name, {})
                        ti = store.get('tenderInfo') or store.get('info')
                        if ti:
                            # Пробуем разные ключи дедлайна
                            dl_str = ti.get('date_end_applications') or ti.get('date_end') or ti.get('endDate')
                            
                            # Если дедлайна в JSON нет, ищем его в HTML
                            if not dl_str:
                                page_text = soup.get_text(separator=' ').replace('\xa0', ' ')
                                page_text = re.sub(r'\s+', ' ', page_text)
                                dl_dt = _search_deadline_in_text(page_text)
                                if not dl_dt:
                                    dl_dt = _search_deadline_in_html_structure(soup, html_content)
                                if dl_dt:
                                    dl_str = dl_dt.isoformat()
                                else:
                                    # DEBUG DUMP PINIA
                                    import time
                                    try:
                                        with open(f"failed_pinia_{ti.get('id', 'unknown')}_{int(time.time())}.html", "w", encoding="utf-8") as f:
                                            f.write(html_content)
                                    except: pass

                            _pc = ti.get('offers_count', 0) or 0
                            if _pc == 0 and _participants_hidden:
                                _pc = -1
                            return {
                                "id": str(ti.get('id', '')),
                                "title": ti.get('name', 'Без названия'),
                                "nmcc": float(ti.get('start_price', 0) or 0),
                                "participants_count": _pc,
                                "deadline": dl_str,
                                "url": current_url
                            }
            except: pass

        # Nuxt формат (включая Nuxt 3 с анонимной функцией)
        if '__NUXT__' in script_text:
            try:
                # Пытаемся найти JSON-подобный объект внутри __NUXT__
                # Если это Nuxt 3, там может быть return { ... } или JSON.parse
                json_match = re.search(r'__NUXT__\s*=\s*(?:.*?return\s*)?({.+?})(?:\s*\(|;|\s*$)', script_text, re.MULTILINE | re.DOTALL)
                if not json_match:
                    # Резервный поиск: ищем просто поля внутри текста скрипта
                    id_match = re.search(r'["\']?id["\']?\s*:\s*(\d+)', script_text)
                    name_match = re.search(r'["\']?(?:name|title)["\']?\s*:\s*["\']([^"\']+)["\']', script_text)
                    date_match = re.search(r'["\']?(?:date_end_applications|date_end|endDate)["\']?\s*:\s*["\']([^"\']+)["\']', script_text)
                    
                    if id_match and name_match:
                        dl_str = date_match.group(1) if date_match else None
                        if not dl_str:
                            # Ищем в тексте страницы
                            page_text = soup.get_text(separator=' ').replace('\xa0', ' ')
                            page_text = re.sub(r'\s+', ' ', page_text)
                            dl_dt = _search_deadline_in_text(page_text)
                            if dl_dt: dl_str = dl_dt.isoformat()
                            
                        return {
                            "id": id_match.group(1),
                            "title": name_match.group(1),
                            "nmcc": 0.0,
                            "participants_count": -1 if _participants_hidden else 0,
                            "deadline": dl_str,
                            "url": current_url
                        }
                
                if json_match:
                    nuxt_data = json.loads(json_match.group(1))
                    if 'data' in nuxt_data:
                        for item in nuxt_data.get('data', []):
                            if isinstance(item, dict) and 'name' in item:
                                dl_str = item.get('date_end_applications') or item.get('date_end')
                                if not dl_str:
                                    page_text = soup.get_text(separator=' ').replace('\xa0', ' ')
                                    page_text = re.sub(r'\s+', ' ', page_text)
                                    dl_dt = _search_deadline_in_text(page_text)
                                    if not dl_dt:
                                        dl_dt = _search_deadline_in_html_structure(soup, html_content)
                                    if dl_dt:
                                        dl_str = dl_dt.isoformat()
                                    else:
                                        import time
                                        try:
                                            with open(f"failed_nuxt_{item.get('id', 'unknown')}_{int(time.time())}.html", "w", encoding="utf-8") as f:
                                                f.write(html_content)
                                        except: pass
                                        
                                _pc = item.get('offers_count', 0) or 0
                                if _pc == 0 and _participants_hidden:
                                    _pc = -1
                                return {
                                    "id": str(item.get('id', '')),
                                    "title": item.get('name', 'Без названия'),
                                    "nmcc": float(item.get('start_price', 0) or 0),
                                    "participants_count": _pc,
                                    "deadline": dl_str,
                                    "url": current_url
                                }
            except: pass

    # 2. Поиск по CSS-селекторам нового дизайна
    title_el = soup.find('h1') or soup.find('div', class_='tender-title') or soup.find('div', class_=re.compile(r'title-and-controls'))
    if title_el:
        title = title_el.get_text(strip=True)
        # Если это реально новый дизайн, заголовок обычно длинный, а контента мало в HTML
        
        # Извлекаем ID из URL
        tender_id = ""
        if current_url:
            tid_match = re.search(r'tender-(\d+)', current_url) or re.search(r'id=(\d+)', current_url)
            if tid_match: tender_id = tid_match.group(1)

        if tender_id:
            # Цена (ищем метку "Общая сумма закупки")
            nmcc = 0.0
            price_label = soup.find(lambda t: t.name in ['div', 'span'] and "Общая сумма закупки" in t.text)
            if price_label:
                parent_text = price_label.parent.get_text()
                price_match = re.search(r'([\d\s,.]+)\s*[₽€\$]', parent_text)
                if price_match:
                    price_str = price_match.group(1).replace("\xa0", "").replace(" ", "").replace(",", ".")
                    # Убираем ведущие/завершающие точки
                    price_str = price_str.strip('.')
                    if price_str:
                        try:
                            nmcc = float(price_str)
                        except ValueError:
                            nmcc = 0.0

            # Участники (Заявки • 5)
            participants = 0
            apps_label = soup.find(lambda t: t.name in ['button', 'span'] and "Заявки •" in t.text)
            if apps_label:
                apps_match = re.search(r'Заявки\s*•\s*(\d+)', apps_label.get_text())
                if apps_match: participants = int(apps_match.group(1))
            if participants == 0 and _participants_hidden:
                participants = -1

            # Дедлайн - ищем по всему тексту страницы
            deadline = None
            # separator=' ' предотвращает склеивание текста из разных тегов
            page_text = soup.get_text(separator=' ')
            # Нормализуем неразрывные пробелы и множественные пробелы
            page_text = page_text.replace('\xa0', ' ')
            page_text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', page_text)
            page_text = re.sub(r'\s+', ' ', page_text)
            
            deadline = _search_deadline_in_text(page_text)
            
            # Если не нашли в тексте, пробуем структурный поиск по HTML
            if not deadline:
                deadline = _search_deadline_in_html_structure(soup, html_content)
                
            if not deadline:
                import time
                try:
                    with open(f"failed_tender_spa_{tender_id}_{int(time.time())}.html", "w", encoding="utf-8") as f:
                        f.write(html_content)
                except: pass

            return {
                "id": tender_id,
                "title": title,
                "nmcc": nmcc,
                "participants_count": participants,
                "deadline": deadline.isoformat() if deadline else None,
                "url": current_url
            }

    # Проверка на пустую SPA-страницу
    page_title = soup.title.string if soup.title else ""
    if page_title and any(x in page_title for x in ["B2B-Center", "Центр электронных торгов"]):
        page_text = soup.get_text(strip=True)
        if len(page_text) < 500: return None

    # --- КЛАССИЧЕСКИЙ ДИЗАЙН ---
    # (ниже идет оригинальная логика)

    # --- Название тендера ---
    title = None
    
    # Сначала пробуем извлечь из мета-тегов (для новых страниц /app/market/)
    og_desc = soup.find('meta', attrs={'data-hid': 'og:description'})
    if og_desc:
        desc_content = og_desc.get('content', '')
        # Формат: "Тендер ... № 4399107 - 21_Электроизделия_Азот_2026"
        title_match = re.search(r'№\s*\d+\s*-\s*(.+)$', desc_content)
        if title_match:
            title = title_match.group(1).strip()
    
    # Если не нашли в мета-тегах, пробуем title
    if not title:
        page_title = soup.title.string if soup.title else ""
        if page_title:
            # Формат: "21_Электроизделия_Азот_2026 — Тендер № 4399107 — ..."
            title_match = re.match(r'^([^—]+)', page_title)
            if title_match:
                potential_title = title_match.group(1).strip()
                if potential_title not in ["B2B-Center", "Центр электронных торгов", ""]:
                    title = potential_title
    
    # Классический парсинг для старых страниц
    if not title:
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
    
    if not title:
        # Ищем в h2 или div с характерными именами классов
        title_tag = soup.find(['h2', 'div', 'span'], class_=re.compile(r'tender-header__title|title|header__name|main-header', re.I))
        if title_tag:
            title = title_tag.get_text(strip=True)
            
    if not title:
        # Пытаемся взять из мета-тегов (OpenGraph)
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content')
            
    if not title:
        # Крайний случай - заголовок страницы (но только если он не дефолтный B2B)
        page_title = soup.title.string if soup.title else ""
        # Убираем " – B2B-Center" из заголовка
        if page_title:
            page_title = page_title.replace(" – B2B-Center", "").replace("– B2B-Center", "").strip()
        # Проверяем что это не дефолтные названия
        if page_title and page_title not in ["B2B-Center", "Центр электронных торгов", ""]:
            title = page_title

    if not title or len(title) < 5 or title in ["Без названия", "Центр электронных торгов"]:
        # Если название совсем короткое или дефолтное, считаем что данные не получены
        return None

    # --- Номер тендера ---
    tender_id = None
    # 1. Из заголовка
    id_match = re.search(r'№\s*(\d+)', title)
    if id_match:
        tender_id = id_match.group(1)
    
    # 2. Из URL (самый надежный способ для B2B)
    if not tender_id and current_url:
        url_match = re.search(r'tender-(\d+)', current_url)
        if url_match:
            tender_id = url_match.group(1)
            
    # 3. Из ID в старой ссылке (view.html?id=...)
    if not tender_id and current_url:
        id_param_match = re.search(r'id=(\d+)', current_url)
        if id_param_match:
            tender_id = id_param_match.group(1)

    if not tender_id:
        return None

    # --- Количество участников ---
    participant_count = 0
    # Ищем паттерн «Поступившие заявки — X», «Всего заявок: X» или «Заявки · X»
    # Поддерживаем разные разделители: —, –, -, :, ·, •
    participants_pattern = re.compile(
        r'(?:Поступивши[ех]\s+заявк[иа]|Всего\s+заявок|Заявки)\s*[—–\-:·•\s]+\s*(\d+)',
        re.IGNORECASE
    )
    # Ищем упоминания того, что участники скрыты
    hidden_pattern = re.compile(
        r'(?:Поступивши[ех]\s+заявк[иа]|Всего\s+заявок|Заявки)\s*[—–\-:·•\s]+\s*(скрыт.*?|недоступ.*?)',
        re.IGNORECASE
    )

    page_text = soup.get_text(separator=' ').replace('\xa0', ' ')

    # Сначала проверяем на скрытые заявки
    if _participants_hidden or hidden_pattern.search(page_text):
        participant_count = -1
    else:
        # Проверяем во вкладках, кнопках и ссылках
        for tag in soup.find_all(['a', 'button', 'span', 'li', 'div', 'td', 'th', 'label']):
            tag_text = tag.get_text(strip=True)
            if hidden_pattern.search(tag_text):
                participant_count = -1
                break
            m = participants_pattern.search(tag_text)
            if m:
                participant_count = int(m.group(1))
                break

        # Если не нашли в тегах, ищем по всему тексту
        if participant_count == 0:
            m = participants_pattern.search(page_text)
            if m:
                participant_count = int(m.group(1))

    # --- Цена (НМЦК) ---
    nmcc = 0.0
    # Ищем паттерны цены: «Начальная цена», «НМЦК», «Стоимость» и т.д.
    price_patterns = [
        re.compile(r'(?:Начальная\s+(?:\(максимальная\)\s+)?цена|НМЦК|Стоимость|Сумма)\s*[:\s]*'
                    r'([\d\s,.]+)', re.IGNORECASE),
        re.compile(r'([\d\s]+[,.][\d]+)\s*(?:руб|₽|RUB)', re.IGNORECASE),
    ]

    page_text = soup.get_text(separator=' ').replace('\xa0', ' ')
    for pattern in price_patterns:
        price_match = pattern.search(page_text)
        if price_match:
            price_str = price_match.group(1).strip()
            # Убираем пробелы, заменяем запятую на точку
            price_str = price_str.replace(' ', '').replace(',', '.')
            try:
                nmcc = float(price_str)
                if nmcc > 0:
                    break
            except ValueError:
                continue

    # --- Дата окончания подачи заявок ---
    deadline = None
    # separator=' ' предотвращает склеивание текста из разных тегов
    page_text = soup.get_text(separator=' ')
    # Нормализуем неразрывные пробелы и множественные пробелы
    page_text = page_text.replace('\xa0', ' ')
    page_text = re.sub(r'\s+', ' ', page_text)

    deadline = _search_deadline_in_text(page_text)

    # Если не нашли в тексте, пробуем структурный поиск по HTML
    if not deadline:
        deadline = _search_deadline_in_html_structure(soup, html_content)
        
    if not deadline:
        import time
        try:
            with open(f"failed_tender_classic_{tender_id}_{int(time.time())}.html", "w", encoding="utf-8") as f:
                f.write(html_content)
        except: pass

    # --- URL ---
    # Если мы получили текущий URL от парсера, используем его (важно для market-next)
    # Если нет, строим классическую ссылку
    if not current_url:
        final_url = f"https://www.b2b-center.ru/market/view.html?id={tender_id}"
    else:
        final_url = current_url

    return {
        "id": tender_id,
        "title": title,
        "nmcc": nmcc,
        "participants_count": participant_count,
        "deadline": deadline.isoformat() if deadline else None,
        "url": final_url,
    }


if __name__ == "__main__":
    import glob
    import os

    for fp in glob.glob(os.path.join("data", "*.html")):
        with open(fp, "r", encoding="utf-8") as f:
            content = f.read()
        result = extract_b2b_data(content)
        print(f"\n--- {os.path.basename(fp)} ---")
        if result:
            print(f"  ID:           {result['id']}")
            print(f"  Название:     {result['title'][:80]}...")
            print(f"  НМЦК:        {result['nmcc']}")
            print(f"  Участников:   {result['participants_count']}")
            print(f"  Дедлайн:     {result['deadline']}")
            print(f"  URL:          {result['url']}")
        else:
            print("  Не удалось извлечь данные (None)")