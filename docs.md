
# Documentation / Документация

## EN

### 1) How the bot works

1. Poll Mastodon notifications for `mention` type (`/api/v1/notifications`).
2. For each mention:
   - Extract plain text from HTML content
   - Remove all mentions like `@giffer` / `@giffer@domain` / `@ giffer`
   - Detect `nsfw` keyword
   - Parse remaining text into tags (supports quotes and `-tag`)
3. Query Furbooru (Philomena API) for GIF images:
   - SFW query adds: `safe, animated, gif`
   - NSFW query adds: `animated, gif` (plus `filter_id` if configured)
4. Pick a random candidate GIF result.
5. Upload media to Mastodon:
   - Try GIF representations in descending size: `full → large → medium → small → thumb`
   - If Mastodon rejects GIF with 422 “not supported” / size limits:
     - Convert GIF → MP4 using ffmpeg
     - Upload MP4 instead
6. Wait for Mastodon media processing to finish (poll `/api/v1/media/:id`):
   - prevents 422 “processing not finished” when posting the status
7. Post reply status with uploaded media:
   - SFW: normal reply
   - NSFW: `sensitive=True` and `spoiler_text="NSFW"` (+ configurable visibility)
8. Save state:
   - `last_seen_notif_id`
   - list of processed `status_id` (to avoid replying twice after restarts)

---

### 2) Query syntax

The bot converts the mention text to Furbooru tags:

- Spaces become multiple tags:  
  `cute fluffy tail` → `cute, fluffy, tail`
- Commas are also supported:  
  `cute, fluffy, tail`
- Quoted phrases stay together as one tag:  
  `"rainbow dash"` → `rainbow dash`
- Negative tags are supported:  
  `-gore` → `-gore`
- `random` / `rnd` are ignored and treated as no user tags (random result).

NSFW mode:
- Any mention containing the word `nsfw` enables NSFW mode and removes that word from the tag list.

---

### 3) Furbooru API notes

Endpoint used:
- `GET /api/v1/json/search/images?q=...&per_page=50&page=1`

The bot filters results:
- `format == "gif"`
- has usable `representations` URL(s)
- chooses randomly among candidates

---

### 4) ALT text generation

ALT text is generated from Furbooru tags:
- removes rating/system tags like `safe`, `nsfw`, `animated`, `gif`
- keeps up to 20 tags
- prefix:
  - `Animated GIF: ...`
  - `NSFW animated GIF: ...`

---

### 5) Rate limiting & anti-spam

Two layers:

1) Per-user cooldown:
- If the same user mentions the bot again within `USER_COOLDOWN_SEC`, bot skips.

2) Global token bucket:
- `GLOBAL_RATE_PER_SEC` tokens refill rate
- `GLOBAL_BURST` max burst
- applies to Furbooru requests

---

### 6) Prevent replying twice (state)

The bot persists state to `STATE_FILE` (default `giffer_state.json`):
- `last_seen_notif_id` (so it won’t re-fetch old notifications after restart)
- `processed_status_ids` (cache to skip duplicates even if server repeats notifications)

If you delete the state file, the bot may reply to old mentions again.

---

### 7) Windows reliability notes

This project includes hardening for Windows:
- `socket.setdefaulttimeout(SOCKET_DEFAULT_TIMEOUT)` to prevent rare indefinite hangs
- `requests.Session` with retries
- explicit connect/read timeouts
- Mastodon API calls are wrapped with a watchdog timeout (thread executor)

---

### 8) Troubleshooting

**Bot replies “media processing not finished” / 422**
- Increase `MEDIA_PROCESS_MAX_WAIT` (e.g. 90)
- Your instance may be slow during peak hours

**GIF rejected: “1440x1440 GIF files are not supported”**
- Expected on some instances
- Bot will fallback to smaller GIF representations
- Then fallback to MP4 conversion

**MP4 conversion fails**
- Install bundled ffmpeg:
  - `py -m pip install imageio-ffmpeg`
- Or install system ffmpeg and ensure it’s in PATH

**Bot replies to old mentions after restart**
- Check `STATE_FILE` exists and is writable
- Don’t delete `giffer_state.json`

---

## RU

### 1) Как работает бот

1. Опрос уведомлений Mastodon типа `mention` (`/api/v1/notifications`).
2. Для каждого упоминания:
   - Достаём текст из HTML
   - Удаляем любые упоминания (`@giffer`, `@giffer@домен`, `@ giffer`)
   - Определяем наличие `nsfw`
   - Парсим оставшееся в теги (есть кавычки и `-тег`)
3. Запрос на Furbooru (Philomena API) за GIF:
   - SFW добавляет: `safe, animated, gif`
   - NSFW добавляет: `animated, gif` (и `filter_id`, если задан)
4. Выбор случайного подходящего результата.
5. Загрузка медиа в Mastodon:
   - Пробуем GIF (full → large → medium → small → thumb)
   - Если инстанс отклоняет GIF с 422 (“not supported”/лимиты):
     - Конвертим GIF → MP4 через ffmpeg
     - Заливаем MP4
6. Ждём завершения обработки медиа в Mastodon (`/api/v1/media/:id`):
   - предотвращает 422 “обработка не окончена” при публикации
7. Публикуем ответ:
   - SFW: обычный ответ
   - NSFW: `sensitive=True` и `spoiler_text="NSFW"` (+ настраиваемая видимость)
8. Сохраняем состояние:
   - `last_seen_notif_id`
   - кэш `status_id` (чтобы не отвечать повторно после рестарта)

---

### 2) Синтаксис запроса (теги)

- Пробелы = несколько тегов:  
  `cute fluffy tail` → `cute, fluffy, tail`
- Запятые тоже можно:  
  `cute, fluffy, tail`
- Кавычки сохраняют фразу как один тег:  
  `"rainbow dash"` → `rainbow dash`
- Минус-теги работают:  
  `-gore` → `-gore`
- `random` / `rnd` игнорируются (рандомный результат).

NSFW-режим:
- Если в тексте есть слово `nsfw`, включается NSFW и это слово убирается из тегов.

---

### 3) Про Furbooru API

Используем:
- `GET /api/v1/json/search/images?q=...&per_page=50&page=1`

Фильтруем результаты:
- `format == "gif"`
- есть рабочие ссылки `representations`
- выбираем случайно

---

### 4) ALT-текст

ALT строится из тегов Furbooru:
- выкидываются системные/рейтинг-теги `safe`, `nsfw`, `animated`, `gif`
- берём до 20 тегов
- префикс:
  - `Animated GIF: ...`
  - `NSFW animated GIF: ...`

---

### 5) Антиспам / Rate-limit

1) Кулдаун на пользователя (`USER_COOLDOWN_SEC`)  
2) Глобальный token-bucket (`GLOBAL_RATE_PER_SEC`, `GLOBAL_BURST`)

---

### 6) Защита от повторных ответов (state)

Файл `STATE_FILE` (по умолчанию `giffer_state.json`) хранит:
- `last_seen_notif_id`
- `processed_status_ids`

Если удалить state-файл — бот может снова отвечать на старые упоминания.

---

### 7) Особенности Windows

Усиленная защита от “вечных зависаний”:
- `socket.setdefaulttimeout(...)`
- `requests.Session` с retry
- явные таймауты connect/read
- watchdog-таймаут вокруг вызовов Mastodon API

---

### 8) Типовые проблемы

**422 “обработка файлов не окончена”**
- увеличь `MEDIA_PROCESS_MAX_WAIT` (например до 90)
- инстанс может быть медленным

**“1440x1440 GIF files are not supported”**
- ожидаемо для некоторых инстансов
- бот уйдёт на меньшие версии GIF, потом на MP4

**Не конвертится в MP4**
- поставь `imageio-ffmpeg`:
  - `py -m pip install imageio-ffmpeg`
- или установи ffmpeg в систему и добавь в PATH

**После рестарта отвечает на старые упоминания**
- проверь, что `giffer_state.json` не удаляется и доступен на запись

---

### 9) Рекомендуемая структура репо

- `giffer_bot.py`
- `README.md`
- `docs.md`
- `.gitignore` (исключить `config.env`, `giffer_state.json`, `*.log`)
- `LICENSE`
