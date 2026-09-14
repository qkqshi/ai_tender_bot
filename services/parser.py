import asyncio
import json
import logging
import math
import os
import random
import re
from typing import List, Optional
from urllib.parse import quote

import httpx

from db.models import TenderBase
from services.b2b_api import B2BApiClient
from tender_logic import extract_b2b_data

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Cache-Control": "max-age=0",
}

SEARCH_BASE_URL = "https://www.b2b-center.ru/market/"


class B2BParser:
    """Ищет тендеры B2B-Center и загружает их данные."""

    def __init__(
        self,
        search_queries: List[str],
        proxy_url: Optional[str] = None,
        admin_id: Optional[int] = None,
        bot=None,
        max_pages: int = 3,
    ):
        self.search_queries = search_queries
        self.proxy_url = proxy_url
        self.admin_id = admin_id
        self.bot = bot
        self.max_pages = max_pages
        self.pagination_warnings: list[str] = []

        self.cookies = self._parse_cookies(os.getenv("B2B_COOKIE"))
        self.storage = self._load_storage()
        self.api = B2BApiClient(
            cookies=self.cookies,
            storage=self.storage,
            proxy_url=self.proxy_url,
        )

    @staticmethod
    def _parse_cookies(cookie_str: Optional[str]) -> dict:
        if not cookie_str:
            return {}
        cookies = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    @staticmethod
    def _load_storage() -> dict:
        """Загружает localStorage и sessionStorage для OIDC."""
        path = os.path.join('data', 'b2b_storage.json')
        if not os.path.exists(path):
            logger.warning(f"{path} не найден — OIDC silent-signin может не пройти")
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            ls_count = len(data.get('localStorage') or {})
            ss_count = len(data.get('sessionStorage') or {})
            logger.info(f"Storage загружен: localStorage={ls_count} ключей, sessionStorage={ss_count}")
            return data
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Ошибка чтения {path}: {e}")
            return {}

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            cookies=self.cookies,
            proxy=self.proxy_url,
            timeout=30.0,
            follow_redirects=True,
        )

    def _is_captcha_page(self, html: str, url: str) -> bool:
        if '/captcha' in url.lower():
            return True

        if 'error=login_required' in url.lower():
            return False
        markers = [
            'smart-captcha', 'smartcaptcha', 'captcha-container',
            'data-sitekey', 'g-recaptcha', 'h-captcha',
            'проверка безопасности', 'проверка браузера',
        ]
        low = html.lower()
        return any(m in low for m in markers)

    def _detect_captcha_info(self, html: str) -> dict:
        for pat, ctype in [
            (r'data-sitekey=["\']([^"\']+)["\']', None),
            (r'["\']?sitekey["\']?\s*[:=]\s*["\']([^"\']+)["\']', 'yandex'),
            (r'["\'](ysc1_[^"\']+)["\']', 'yandex'),
        ]:
            m = re.search(pat, html)
            if m:
                sk = m.group(1)
                if ctype is None:
                    ctype = 'recaptcha' if 'g-recaptcha' in html.lower() else 'yandex'
                return {'type': ctype, 'sitekey': sk}
        return {'type': None, 'sitekey': None}

    async def _request_captcha_solution(
        self, captcha_type: str, website_url: str, sitekey: str,
    ) -> Optional[str]:
        if captcha_type == 'yandex':
            api_key = os.getenv("RUCAPTCHA_API_KEY")
            api_url = "https://api.rucaptcha.com"
            task = {"type": "YandexSmartCaptchaTaskProxyless", "websiteURL": website_url, "websiteKey": sitekey}
            service_name = "RuCaptcha"
        elif captcha_type == 'recaptcha':
            api_key = os.getenv("ANTICAPTCHA_API_KEY")
            api_url = "https://api.anti-captcha.com"
            task = {"type": "RecaptchaV2TaskProxyless", "websiteURL": website_url, "websiteKey": sitekey}
            service_name = "Anti-Captcha"
        else:
            logger.error(f"Неподдерживаемый тип капчи: {captcha_type}")
            return None

        if not api_key:
            logger.error(f"API-ключ для {service_name} не задан в .env")
            return None

        logger.info(f"Решаем капчу через {service_name} ({captcha_type}), sitekey={sitekey[:20]}...")
        try:
            async with httpx.AsyncClient(timeout=180.0) as ac:
                resp = await ac.post(f"{api_url}/createTask", json={"clientKey": api_key, "task": task})
                data = resp.json()
                if data.get("errorId") != 0:
                    logger.error(f"{service_name} create: {data.get('errorDescription')}")
                    return None
                task_id = data["taskId"]

                for _ in range(60):
                    await asyncio.sleep(3)
                    r = await ac.post(f"{api_url}/getTaskResult", json={"clientKey": api_key, "taskId": task_id})
                    rd = r.json()
                    if rd.get("status") == "ready":
                        token = rd.get("solution", {}).get("token")
                        if token:
                            logger.info(f"Капча решена через {service_name}")
                            return token
                        return None
                    if rd.get("errorId") != 0:
                        logger.error(f"{service_name}: {rd.get('errorDescription')}")
                        return None
                logger.error(f"Таймаут решения капчи через {service_name}")
                return None
        except Exception as e:
            logger.error(f"Ошибка {service_name}: {e}")
            return None

    async def _submit_captcha_solution(
        self, client: httpx.AsyncClient,
        captcha_html: str, captcha_url: str, original_url: str, token: str,
    ) -> bool:
        hidden = {}
        for m in re.finditer(
            r'<input[^>]+type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
            captcha_html, re.I,
        ):
            hidden[m.group(1)] = m.group(2)
        hidden['smart-token'] = token
        hidden['g-recaptcha-response'] = token

        fm = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', captcha_html, re.I)
        if fm:
            action = fm.group(1)
            if not action.startswith('http'):
                action = f"https://www.b2b-center.ru{action}"
            try:
                resp = await client.post(action, data=hidden)
                if '/captcha' not in str(resp.url).lower():
                    return True
            except Exception as e:
                logger.warning(f"POST form: {e}")

        try:
            resp = await client.post(captcha_url, data=hidden)
            if '/captcha' not in str(resp.url).lower():
                return True
        except Exception as e:
            logger.warning(f"POST captcha URL: {e}")

        try:
            sep = "&" if "?" in original_url else "?"
            resp = await client.get(f"{original_url}{sep}smart-token={token}")
            if '/captcha' not in str(resp.url).lower() and resp.status_code == 200:
                return True
        except Exception as e:
            logger.warning(f"GET smart-token: {e}")

        return False

    async def _alert_admin(self, message: str):
        if self.bot and self.admin_id:
            try:
                await self.bot.send_message(self.admin_id, message)
            except Exception as e:
                logger.error(f"Не удалось отправить алерт админу: {e}")

    async def _get_search_html(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        """Загружает HTML страницы поиска."""
        try:
            response = await client.get(url)
            html = response.text
            final_url = str(response.url)

            if response.status_code == 403 and not self._is_captcha_page(html, final_url):
                await self._alert_admin(f"⛔ 403 Forbidden:\n{url}")
                return None
            if response.status_code == 429:
                await self._alert_admin(f"⏳ 429 Too Many Requests:\n{url}")
                return None

            if self._is_captcha_page(html, final_url):
                logger.warning(f"Капча на поиске: {final_url}")
                info = self._detect_captcha_info(html)
                if info['type'] and info['sitekey']:
                    token = await self._request_captcha_solution(info['type'], final_url, info['sitekey'])
                    if token:
                        ok = await self._submit_captcha_solution(client, html, final_url, url, token)
                        if ok:
                            await asyncio.sleep(random.uniform(2.0, 4.0))
                            retry = await client.get(url)
                            if retry.status_code == 200 and not self._is_captcha_page(retry.text, str(retry.url)):
                                return retry.text
                await self._alert_admin(f"🤖 Капча не решена:\n{url}")
                return None

            if response.status_code != 200:
                logger.error(f"HTTP {response.status_code}: {url}")
                return None

            if "войти" in html.lower() and "личный кабинет" not in html.lower():
                logger.warning("Сессия гостя — проверь B2B_COOKIE")

            if self._is_servicepipe_challenge(html):
                logger.warning(f"ServicePipe challenge на странице ({len(html)} байт), пробиваем через Playwright: {url}")
                try:
                    fresh_html = await self.api.fetch_html_via_playwright(url)
                except Exception as e:
                    logger.error(f"Playwright fallback упал на {url}: {e}")
                    return None
                if not fresh_html:
                    logger.warning(f"Playwright не вернул HTML после SP-challenge: {url}")
                    return None

                for k, v in self.api.cookies.items():
                    client.cookies.set(k, v, domain=".b2b-center.ru")
                    self.cookies[k] = v
                logger.info(f"SP-challenge пробит, html len={len(fresh_html)}")
                return fresh_html

            return html
        except httpx.TimeoutException:
            logger.error(f"Таймаут: {url}")
            return None
        except httpx.ConnectError as e:
            logger.error(f"Ошибка подключения: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка загрузки {url}: {e}")
            return None

    @staticmethod
    def _is_servicepipe_challenge(html: str) -> bool:
        """Проверяет ответ на наличие ServicePipe challenge."""
        if not html or len(html) > 5000:
            return False
        low = html.lower()
        return 'servicepipe' in low or '/checkjs/' in low

    async def _fetch_old_html(
        self, client: httpx.AsyncClient, tid: str, url: str,
    ) -> Optional[dict]:
        """Загружает и разбирает страницу тендера старого формата."""
        try:
            html = await self._get_search_html(client, url)
        except Exception as e:
            logger.error(f"Ошибка загрузки HTML старого тендера {tid}: {e}")
            return None
        if not html:
            return None

        if self._is_servicepipe_challenge(html):
            logger.info(f"ServicePipe challenge для {tid}, пробиваем через Playwright")
            try:
                html = await self.api.fetch_html_via_playwright(url)
            except Exception as e:
                logger.error(f"Playwright для {tid} упал: {e}")
                return None
            if not html:
                logger.warning(f"Playwright не вернул HTML для {tid}")
                return None

            for k, v in self.api.cookies.items():
                client.cookies.set(k, v, domain=".b2b-center.ru")
                self.cookies[k] = v

        data = extract_b2b_data(html, url)
        if not data:
            low = html.lower()
            is_guest = 'войти' in low and 'личный кабинет' not in low
            has_h1 = '<h1' in low
            has_title = '<title' in low
            reason = []
            if is_guest:
                reason.append('сессия гостя — протухли куки')
            if not has_h1:
                reason.append('нет h1')
            if not has_title:
                reason.append('нет title')
            logger.warning(
                f"extract_b2b_data=None для старого тендера {tid}: "
                f"{', '.join(reason) or 'неизвестная причина'} (len={len(html)})"
            )
            try:
                with open(f"failed_old_{tid}.html", "w", encoding="utf-8") as f:
                    f.write(html)
            except OSError:
                pass
            return None

        return {
            "id": str(data.get("id") or tid),
            "title": data.get("title") or "Без названия",
            "nmcc": float(data.get("nmcc") or 0.0),
            "participants_count": int(data.get("participants_count") or 0),
            "deadline": data.get("deadline"),
            "url": data.get("url") or url,
        }

    @staticmethod
    def _extract_tender_id(url: str) -> Optional[str]:
        """Извлекает идентификатор тендера из URL."""
        m = re.search(r'/tender-(\d+)', url) or re.search(r'[?&]id=(\d+)', url)
        return m.group(1) if m else None

    async def fetch_tenders(self) -> List[TenderBase]:
        """Ищет и возвращает тендеры по текущим настройкам."""
        tenders: list[TenderBase] = []
        seen_ids: set[str] = set()
        total_found = 0
        total_unique = 0
        total_dups = 0
        total_parsed = 0
        total_failed = 0

        no_keyword_mode = not self.search_queries
        queries = self.search_queries if not no_keyword_mode else [""]
        effective_cap = min(self.max_pages, 50) if no_keyword_mode else self.max_pages

        try:
            async with self._build_client() as client:
                for query in queries:
                    encoded_query = quote(query) if query else ""
                    effective_max_pages = effective_cap

                    for page in range(1, effective_cap + 1):
                        if page > effective_max_pages:
                            break

                        from_offset = (page - 1) * 20
                        if no_keyword_mode:
                            search_url = (
                                f"{SEARCH_BASE_URL}?searching=1&trade=buy&from={from_offset}"
                            )
                        else:
                            search_url = (
                                f"{SEARCH_BASE_URL}?f_keyword={encoded_query}"
                                f"&searching=1&trade=buy&from={from_offset}"
                            )

                        if page > 1:
                            await asyncio.sleep(random.uniform(2.0, 4.0))

                        log_label = query if query else "(вся лента)"
                        logger.info(f"Поиск '{log_label}' (стр. {page})")
                        html = await self._get_search_html(client, search_url)
                        if not html:
                            reason = (
                                f"⚠️ Поиск «{log_label}» прерван на стр. {page}: "
                                f"не удалось загрузить страницу (капча / 403 / 429 / SP-challenge — см. логи)."
                            )
                            self.pagination_warnings.append(reason)
                            logger.warning(reason)
                            break

                        if page == 1 and not no_keyword_mode:
                            total_count_match = (
                                re.search(r'Актуально\s*•\s*([\d\s]+)', html)
                                or re.search(r'Актуальных лотов:\s*([\d\s]+)', html)
                            )
                            if total_count_match:
                                try:
                                    total_items = int(
                                        total_count_match.group(1).replace("\xa0", "").replace(" ", "")
                                    )
                                    total_pages_available = math.ceil(total_items / 20)
                                    logger.info(
                                        f"Маркер на стр.1: всего {total_items} лотов "
                                        f"≈ {total_pages_available} стр. (запрошено {self.max_pages})"
                                    )
                                    if self.max_pages > total_pages_available:
                                        warning = (
                                            f"⚠️ По запросу «{query}» доступно всего "
                                            f"{total_pages_available} стр. (найдено {total_items} лотов), "
                                            f"буду искать {total_pages_available} стр."
                                        )
                                        self.pagination_warnings.append(warning)
                                        logger.info(f"{warning}")
                                        effective_max_pages = total_pages_available
                                except Exception as e:
                                    logger.error(f"Ошибка парсинга общего количества страниц: {e}")

                        ids_with_urls: list[tuple[str, str]] = []
                        for m in re.finditer(r'href=["\']([^"\']*?/tender-(\d+)/[^"\']*?)["\']', html):
                            tid = m.group(2)
                            rel = m.group(1)
                            full = rel if rel.startswith("http") else f"https://www.b2b-center.ru{rel}"
                            ids_with_urls.append((tid, full))
                        for m in re.finditer(r'href=["\']([^"\']*?view\.html\?id=(\d+)[^"\']*?)["\']', html):
                            tid = m.group(2)
                            rel = m.group(1)
                            full = rel if rel.startswith("http") else f"https://www.b2b-center.ru{rel}"
                            ids_with_urls.append((tid, full))

                        if not ids_with_urls:
                            reason = (
                                f"⚠️ Поиск «{log_label}» прерван на стр. {page}: "
                                f"страница загружена ({len(html)} байт), но не нашёл "
                                f"ни одной ссылки на тендер. Возможно, выдача закончилась "
                                f"или вёрстка изменилась."
                            )
                            self.pagination_warnings.append(reason)
                            logger.warning(reason)
                            try:
                                fn = f"empty_search_{page}.html"
                                with open(fn, "w", encoding="utf-8") as f:
                                    f.write(html)
                                logger.info(f"HTML страницы сохранён в {fn} для разбора")
                            except OSError:
                                pass
                            break

                        page_unique = sum(1 for tid, _ in ids_with_urls if tid not in seen_ids)
                        page_dups = len(ids_with_urls) - page_unique
                        logger.info(
                            f"Стр. {page}: {len(ids_with_urls)} ссылок "
                            f"(уник: {page_unique}, дублей с пред. стр.: {page_dups})"
                        )
                        total_found += len(ids_with_urls)
                        total_unique += page_unique
                        total_dups += page_dups

                        for tid, url in ids_with_urls:
                            if tid in seen_ids:
                                logger.debug(f"Дубль {tid} (уже видели на пред. стр.) — скип")
                                continue

                            await asyncio.sleep(random.uniform(1.5, 3.5))

                            is_old_design = 'view.html?id=' in url
                            if is_old_design:
                                info = await self._fetch_old_html(client, tid, url)
                            else:
                                info = await self.api.get_tender(tid, tender_url=url)
                            if not info:
                                logger.warning(f"Не удалось получить тендер {tid}")
                                total_failed += 1

                                seen_ids.add(tid)
                                continue

                            final_id = str(info.get("id") or tid)
                            if final_id in seen_ids:
                                continue
                            seen_ids.add(final_id)

                            tenders.append(TenderBase(
                                id=final_id,
                                title=info.get("title", "Без названия"),
                                nmcc=float(info.get("nmcc", 0.0)),
                                participant_count=info.get("participants_count", 0),
                                is_online=True,
                                platform="b2b-center",
                                url=info.get("url") or url,
                                deadline=info.get("deadline"),
                                raw_json=info,
                            ))
                            logger.info(
                                f"Tender {final_id}: {info.get('title', '')[:40]}... | "
                                f"deadline={info.get('deadline')}"
                            )
                            total_parsed += 1
        finally:
            await self.api.close()

        pct_uniq = (total_parsed * 100 // total_unique) if total_unique > 0 else 0
        logger.info(
            f"Статистика: всего ссылок {total_found} "
            f"(уник: {total_unique}, дублей между стр.: {total_dups}); "
            f"распарсено {total_parsed}, ошибок {total_failed} "
            f"({pct_uniq}% от уникальных)"
        )
        return tenders
