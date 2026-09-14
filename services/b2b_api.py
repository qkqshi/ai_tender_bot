"""Клиент API B2B-Center."""
import asyncio
import json
import logging
import re
import time
from typing import Any, Optional, Tuple

import httpx
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

API_ENDPOINT = "https://www.b2b-center.ru/market/openapi/trade/get_trade_aggregate"

TRADE_AGGREGATE_FIELDS = [
    {"field_name": "trade"},
    {"field_name": "current_stage"},
    {"field_name": "stages_list"},
    {"field_name": "positions_count"},
    {"field_name": "trade_participants_count"},
    {"field_name": "main_stage_offers_count"},
]


def _get_eav_value(item: dict) -> Any:
    """Достаёт значение из EAV-записи (приоритет по типу)."""
    if not isinstance(item, dict):
        return None
    for key in ("string_value", "date_time", "int_value", "float_value", "bool_value"):
        v = item.get(key)
        if v is not None:
            return v
    return None


def _find_eav_by_sys_name(eav_list: list, sys_name: str) -> Optional[dict]:
    """Ищет EAV-запись по sys_name."""
    if not isinstance(eav_list, list):
        return None
    for item in eav_list:
        if isinstance(item, dict) and (item.get("sys_name") == sys_name or item.get("field_sys_name") == sys_name):
            return item
    return None


class B2BApiClient:
    """Получает и преобразует данные тендеров B2B-Center."""

    TOKEN_REFRESH_BUFFER_SEC = 60

    def __init__(
        self,
        cookies: dict,
        storage: dict,
        proxy_url: Optional[str] = None,
    ):
        self.cookies = dict(cookies or {})
        self.storage = storage or {}
        self.proxy_url = proxy_url

        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def close(self):
        """Поддерживает общий интерфейс клиентов."""
        pass

    def _is_token_valid(self) -> bool:
        return bool(self._token) and (time.time() < self._token_expires_at - self.TOKEN_REFRESH_BUFFER_SEC)

    async def _get_token(self, trigger_url: str) -> Optional[str]:
        """Возвращает валидный токен, при необходимости обновляет через Playwright."""
        async with self._token_lock:
            if self._is_token_valid():
                return self._token

            logger.info("Получаем fresh access_token через Playwright...")
            token, expires_in, updated_cookies = await self._fetch_fresh_token(trigger_url)
            if not token:
                logger.error("Не удалось получить access_token")
                return None

            self._token = token
            self._token_expires_at = time.time() + (expires_in or 300)

            if updated_cookies:
                self.cookies.update(updated_cookies)
            logger.info(
                f"Token получен (len={len(token)}, expires_in={expires_in}s, "
                f"куки обновлены: {len(updated_cookies) if updated_cookies else 0})"
            )
            return self._token

    async def _fetch_fresh_token(self, trigger_url: str) -> Tuple[Optional[str], Optional[int], dict]:
        """Получает OIDC-токен через браузерную сессию."""
        async with async_playwright() as pw:
            launch_opts = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            }
            if self.proxy_url:
                m = re.match(r"https?://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)", self.proxy_url)
                if m:
                    user, pwd, host, port = m.groups()
                    launch_opts["proxy"] = {"server": f"http://{host}:{port}"}
                    if user and pwd:
                        launch_opts["proxy"]["username"] = user
                        launch_opts["proxy"]["password"] = pwd

            browser = await pw.chromium.launch(**launch_opts)
            try:
                context = await browser.new_context(user_agent=UA, locale="ru-RU")

                if self.cookies:
                    await context.add_cookies([
                        {"name": k, "value": v, "domain": ".b2b-center.ru", "path": "/"}
                        for k, v in self.cookies.items()
                    ])

                if self.storage:
                    ls = json.dumps(self.storage.get("localStorage") or {}, ensure_ascii=False)
                    ss = json.dumps(self.storage.get("sessionStorage") or {}, ensure_ascii=False)
                    await context.add_init_script(f"""
                        (function() {{
                            try {{ const ls = {ls};
                                for (const k in ls) {{ try {{ localStorage.setItem(k, ls[k]); }} catch(e) {{}} }}
                            }} catch(e) {{}}
                            try {{ const ss = {ss};
                                for (const k in ss) {{ try {{ sessionStorage.setItem(k, ss[k]); }} catch(e) {{}} }}
                            }} catch(e) {{}}
                        }})();
                    """)

                page = await context.new_page()

                token_holder: dict = {"token": None, "expires_in": None}
                token_event = asyncio.Event()

                seen_requests: list[dict] = []
                console_logs: list[str] = []

                def _on_request(req):
                    """Перехватывает Bearer-токен из запросов страницы."""
                    if token_holder["token"]:
                        return
                    try:
                        u = req.url.lower()
                        if "/market/openapi/" in u or "/api/openid/" in u:
                            auth = (req.headers or {}).get("authorization") or (req.headers or {}).get("Authorization")
                            if auth and auth.lower().startswith("bearer "):
                                tok = auth.split(" ", 1)[1].strip()
                                if tok:
                                    token_holder["token"] = tok

                                    token_holder["expires_in"] = token_holder.get("expires_in") or 300
                                    token_event.set()
                                    logger.info(f"Token снят из Authorization-заголовка {req.url[:100]}")
                    except Exception as e:
                        logger.warning(f"on_request hook error: {e}")

                async def _on_resp(resp):
                    u = resp.url
                    ul = u.lower()
                    try:
                        seen_requests.append({
                            "url": u, "status": resp.status, "method": resp.request.method,
                        })
                    except Exception:
                        pass

                    if any(s in ul for s in ("/auth/", "/openid/", "token", "oauth")):
                        logger.info(f"auth-resp: {resp.status} {u[:160]}")

                    if "/auth/openid/token/" in ul and resp.status == 200:
                        try:
                            body = await resp.text()
                            data = json.loads(body)
                            tok = data.get("access_token")
                            if tok and not token_holder["token"]:
                                token_holder["token"] = tok
                                token_holder["expires_in"] = data.get("expires_in")
                                token_event.set()
                        except Exception as e:
                            logger.warning(f"Не смог распарсить OIDC-ответ: {e}")

                page.on("request", _on_request)
                page.on("response", _on_resp)
                page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text[:300]}"))

                logger.info(f"trigger_url: {trigger_url}")

                try:
                    await page.goto(trigger_url, wait_until="domcontentloaded", timeout=45000)
                    logger.info(f"final_url после goto: {page.url}")
                except Exception as e:
                    logger.warning(f"goto warning: {e}")

                try:
                    await asyncio.wait_for(token_event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning("Токен не перехвачен за 30 сек — возможно, storage/cookies протухли")

                    try:
                        tid_match = re.search(r"tender-(\d+)|[?&]id=(\d+)", trigger_url)
                        tid = (tid_match.group(1) or tid_match.group(2)) if tid_match else "unknown"
                        debug_path = f"token_debug_{tid}.log"
                        page_html = ""
                        try:
                            page_html = await page.content()
                        except Exception:
                            pass
                        with open(debug_path, "w", encoding="utf-8") as f:
                            f.write(f"trigger_url: {trigger_url}\n")
                            f.write(f"final_url:   {page.url}\n")
                            f.write(f"\n===== Network requests ({len(seen_requests)}) =====\n")
                            for r in seen_requests:
                                f.write(f"{r['status']} {r['method']} {r['url']}\n")
                            f.write(f"\n===== Console logs ({len(console_logs)}) =====\n")
                            for line in console_logs:
                                f.write(line + "\n")
                            f.write(f"\n===== Page HTML (len={len(page_html)}) =====\n")
                            f.write(page_html[:50000])
                        logger.info(
                            f"Дамп для отладки токена: {debug_path} "
                            f"(requests={len(seen_requests)}, console={len(console_logs)}, "
                            f"html_len={len(page_html)})"
                        )
                        try:
                            await page.screenshot(path=f"token_debug_{tid}.png", full_page=True)
                            logger.info(f"Скриншот: token_debug_{tid}.png")
                        except Exception as e:
                            logger.warning(f"Не смог снять скриншот: {e}")
                    except Exception as e:
                        logger.warning(f"Не смог записать debug-дамп: {e}")

                all_cookies = await context.cookies()
                final_cookies = {c["name"]: c["value"] for c in all_cookies}

                return token_holder["token"], token_holder["expires_in"], final_cookies
            finally:
                await browser.close()

    async def fetch_html_via_playwright(self, url: str, timeout_ms: int = 30000) -> Optional[str]:
        """Загружает HTML через Playwright и обновляет cookies."""
        async with async_playwright() as pw:
            launch_opts = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            }
            if self.proxy_url:
                m = re.match(r"https?://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)", self.proxy_url)
                if m:
                    user, pwd, host, port = m.groups()
                    launch_opts["proxy"] = {"server": f"http://{host}:{port}"}
                    if user and pwd:
                        launch_opts["proxy"]["username"] = user
                        launch_opts["proxy"]["password"] = pwd

            browser = await pw.chromium.launch(**launch_opts)
            try:
                context = await browser.new_context(user_agent=UA, locale="ru-RU")
                if self.cookies:
                    await context.add_cookies([
                        {"name": k, "value": v, "domain": ".b2b-center.ru", "path": "/"}
                        for k, v in self.cookies.items()
                    ])
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                    await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except Exception as e:
                    logger.warning(f"Playwright goto warning для {url}: {e}")

                html = await page.content()

                all_cookies = await context.cookies()
                fresh = {c["name"]: c["value"] for c in all_cookies}
                if fresh:
                    self.cookies.update(fresh)
                return html
            finally:
                await browser.close()

    async def get_tender(self, tender_id: int | str, tender_url: Optional[str] = None) -> Optional[dict]:
        """Возвращает данные тендера или None при ошибке."""
        tender_id = int(tender_id)
        if not tender_url:
            tender_url = f"https://www.b2b-center.ru/app/market/tender-{tender_id}/"

        token = await self._get_token(tender_url)
        if not token:
            return None

        headers = {
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Content-Type": "application/json",
            "User-Agent": UA,
            "Origin": "https://www.b2b-center.ru",
            "Referer": tender_url,
            "Authorization": f"Bearer {token}",
        }
        body = {
            "trade_id": {"value": tender_id},
            "filter": {"fields": TRADE_AGGREGATE_FIELDS},
        }

        last_err: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=30.0, cookies=self.cookies, proxy=self.proxy_url) as client:
                    resp = await client.post(API_ENDPOINT, headers=headers, json=body)
                    if resp.status_code == 401:
                        logger.warning("401 от API — сбрасываем токен и пробуем повторно")
                        self._token = None
                        self._token_expires_at = 0.0
                        token = await self._get_token(tender_url)
                        if not token:
                            return None
                        headers["Authorization"] = f"Bearer {token}"
                        resp = await client.post(API_ENDPOINT, headers=headers, json=body)

                    if resp.status_code != 200:
                        logger.error(f"API {resp.status_code} для тендера {tender_id}: {resp.text[:300]}")
                        return None

                    data = resp.json()
                    break
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
                last_err = e
                if attempt == 1:
                    logger.warning(f"Сетевая ошибка для {tender_id} ({e}), ретрай через 2 сек")
                    await asyncio.sleep(2.0)
                    continue
                logger.error(f"Сетевая ошибка для тендера {tender_id} после ретрая: {e}")
                return None
            except Exception as e:
                logger.error(f"Ошибка API для тендера {tender_id}: {e}")
            return None

        return self.extract_tender(data, tender_url)

    @staticmethod
    def extract_tender(api_response: dict, tender_url: str) -> Optional[dict]:
        """Преобразует EAV-ответ API в данные тендера."""
        ta = api_response.get("trade_aggregate") or api_response
        if not isinstance(ta, dict):
            return None

        trade = ta.get("trade") or {}
        trade_fv = ((trade.get("fields_values") or {}).get("fields_values")) or []

        tid_obj = trade.get("id")
        if isinstance(tid_obj, dict):
            tender_id = tid_obj.get("value")
        else:
            tender_id = tid_obj
        if tender_id is None:
            sv = ((trade.get("settings_values") or {}).get("fields_values")) or []
            tn = _find_eav_by_sys_name(sv, "trade_number")
            if tn:
                tender_id = _get_eav_value(tn)
        if tender_id is None:
            logger.warning("extract: не нашёл trade.id")
            return None

        subj = _find_eav_by_sys_name(trade_fv, "subject")
        title = _get_eav_value(subj) if subj else None
        if not title:
            logger.warning(f"extract: тендер {tender_id} без subject")
            title = "Без названия"

        deadline = None
        de = _find_eav_by_sys_name(trade_fv, "offers_stage_date_end")
        if de:
            deadline = _get_eav_value(de)
        if not deadline:
            cs = ta.get("current_stage") or {}
            cs_fv = ((cs.get("fields_values") or {}).get("fields_values")) or []
            de2 = _find_eav_by_sys_name(cs_fv, "date_end")
            if de2:
                deadline = _get_eav_value(de2)
        if not deadline:
            sv = ((trade.get("settings_values") or {}).get("fields_values")) or []
            de3 = _find_eav_by_sys_name(sv, "main_stage_date_end")
            if de3:
                deadline = _get_eav_value(de3)

        if not deadline:
            logger.warning(f"extract: тендер {tender_id} без deadline — пропускаем")
            return None

        deadline_str = str(deadline)

        sv = ((trade.get("settings_values") or {}).get("fields_values")) or []

        hide_p_field = _find_eav_by_sys_name(trade_fv, "hide_participants_count")
        is_hidden = bool(hide_p_field and _get_eav_value(hide_p_field) is True)

        if is_hidden:
            participants_count = -1
        else:
            real_p = _find_eav_by_sys_name(sv, "participants_count")
            if real_p is not None and _get_eav_value(real_p) is not None:
                try:
                    participants_count = int(_get_eav_value(real_p))
                except (TypeError, ValueError):
                    participants_count = 0
            else:
                participants_count = ta.get("trade_participants_count") or 0
                try:
                    participants_count = int(participants_count)
                except (TypeError, ValueError):
                    participants_count = 0

        nmcc = 0.0

        return {
            "id": str(tender_id),
            "title": str(title).strip() or "Без названия",
            "nmcc": nmcc,
            "participants_count": participants_count,
            "deadline": deadline_str,
            "url": tender_url,
        }
