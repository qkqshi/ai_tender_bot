# Как экспортировать localStorage из браузера

SPA B2B-Center хранит OAuth/OIDC токены в `localStorage` и `sessionStorage`.
Без них Playwright не может получить данные тендера (запрос `/auth/openid/token/` отвечает 400).

## Шаги

1. Открой в Chrome/Firefox авторизованную сессию на b2b-center.ru
2. Открой любую страницу тендера `/app/market/.../tender-XXXXXXX/` — чтобы OIDC-клиент успел
   создать все нужные ключи storage
3. Нажми F12 → вкладка **Console**
4. Вставь и выполни:

```js
copy(JSON.stringify({
  localStorage: Object.fromEntries(Object.entries(localStorage)),
  sessionStorage: Object.fromEntries(Object.entries(sessionStorage)),
}, null, 2));
console.log("✅ Storage скопирован в буфер обмена");
```

5. Открой `data/b2b_storage.json` (создай, если нет) и вставь из буфера
6. Сохрани файл

## Важно

- Файл содержит access_token / refresh_token — **не публикуй в git**
- Токены живут ~1 час. Когда бот перестанет видеть дедлайны — повтори экспорт
- Куки в `.env` (`B2B_COOKIE`) тоже нужны — экспортируй их параллельно
