# furry_giffer_bot_mastodon

**EN:** A Mastodon mention-bot that replies with animated media from **Furbooru** (Philomena API).  
It tries to upload a GIF first (with size fallbacks) and, if your Mastodon instance rejects GIFs (e.g. resolution limits), it automatically converts GIF → **MP4** and uploads that instead.  
For NSFW requests it posts with **CW + spoiler** and marks media as sensitive.  
Works on **Windows** and Linux.

**RU:** Бот для Mastodon, который отвечает на упоминания и прикрепляет анимированное медиа из **Furbooru** (Philomena API).  
Сначала пробует залить GIF (с фолбэком на меньшие версии), а если инстанс отклоняет GIF (например из-за лимитов на разрешение) — автоматически конвертирует GIF → **MP4** и заливает MP4.  
Для NSFW-запросов постит с **CW + спойлером** и отмечает медиа как sensitive.  
Работает на **Windows** и Linux.

---

## Features / Возможности

- Reply to mentions: `@giffer cute fluffy tail`
- Multi-tag queries: spaces/commas, quotes, negative tags  
  - `cute fluffy tail` → `cute, fluffy, tail`  
  - `"rainbow dash" -gore`
- `nsfw` keyword enables NSFW mode:
  - posts with `spoiler_text="NSFW"` and `sensitive=True`
  - uses NSFW filter_id if configured
- Furbooru search for **animated GIF** via Philomena API
- Real upload to Mastodon
- Waits for media processing (prevents 422 “processing not finished”)
- GIF upload fallbacks: full → large → medium → small → thumb
- If GIF rejected by instance: **auto-convert to MP4**
- Auto ALT text from Furbooru tags
- Rate-limit protection:
  - per-user cooldown
  - global token bucket
- Persistent state (prevents replying twice after restart):
  - saves last seen notification id + processed status ids
- Logs to console + rotating file

---

## Requirements / Требования

- Python 3.10+ recommended
- Mastodon access token with permission to read notifications & post statuses/media
- Optional: `ffmpeg` (or `imageio-ffmpeg` for bundled ffmpeg)

---

## Install / Установка

### Windows (PowerShell or CMD)
```bat
py -m pip install --upgrade pip
py -m pip install Mastodon.py requests imageio-ffmpeg
````

### Linux/macOS

```bash
python3 -m pip install --upgrade pip
python3 -m pip install Mastodon.py requests imageio-ffmpeg
```

> If you already have system `ffmpeg`, you can skip `imageio-ffmpeg`, but it’s recommended for Windows.

---

## Configuration / Настройка

Create `config.env` next to the script:

```env
MASTODON_BASE_URL=https://bronyfurry.com
MASTODON_ACCESS_TOKEN=YOUR_TOKEN_HERE

FURBOORU_BASE_URL=https://furbooru.org
# FURBOORU_API_KEY=OPTIONAL
# SAFE_FILTER_ID=OPTIONAL
# NSFW_FILTER_ID=OPTIONAL

# Optional runtime tuning:
CHECK_INTERVAL=30
USER_COOLDOWN_SEC=20
GLOBAL_RATE_PER_SEC=1.0
GLOBAL_BURST=3

LOG_LEVEL=INFO
LOG_FILE=giffer.log

STATE_FILE=giffer_state.json
PROCESSED_CACHE_MAX=800

# Network tuning (Windows-safe defaults):
CONNECT_TIMEOUT=5
READ_TIMEOUT=20
DOWNLOAD_TIMEOUT=40
SOCKET_DEFAULT_TIMEOUT=25
MASTO_CALL_TIMEOUT=25
MEDIA_PROCESS_MAX_WAIT=60

# NSFW posts visibility:
NSFW_VISIBILITY=public
```

---

## Run / Запуск

```bash
python giffer_bot.py
```

The bot will continuously poll mentions and respond.

---

## Usage / Использование

Mention the bot with tags:

* `@giffer cute`
* `@giffer cute fluffy tail`
* `@giffer "rainbow dash" -gore`
* `@giffer nsfw latex`

NSFW mode is triggered if the word `nsfw` is present anywhere in the mention content.

The reply will include an **original source link** (Furbooru page).

---

## Files / Файлы

* `giffer_bot.py` — bot source code
* `config.env` — configuration (not committed)
* `giffer.log` — log file (rotating)
* `giffer_state.json` — saved state to avoid double replies

---

## Notes / Примечания

* If the bot starts replying to old mentions again, make sure `STATE_FILE` is not deleted.
* Some Mastodon instances reject large GIFs. This is expected; the bot will try smaller representations and then MP4 fallback.

---

## License / Лицензия

MIT License
