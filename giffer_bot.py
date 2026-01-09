#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import re
import time
import random
import logging
import socket
import tempfile
import subprocess
import json
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple, Dict, Any, Callable, TypeVar, List
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None  # type: ignore

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None  # type: ignore


# =======================
# ENV loader (Windows-friendly)
# =======================

def load_env_file(path: str = "config.env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if "=" not in line:
                continue

            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()

            if v and v[0] not in "\"'":
                m = re.search(r"\s+#", v)
                if m:
                    v = v[: m.start()].rstrip()

            v = v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)


load_env_file()


# =======================
# CONFIG
# =======================

MASTODON_BASE_URL = os.getenv("MASTODON_BASE_URL", "https://bronyfurry.com").strip().rstrip("/")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN", "").strip()

FURBOORU_BASE_URL = os.getenv("FURBOORU_BASE_URL", "https://furbooru.org").strip().rstrip("/")
FURBOORU_API_KEY = os.getenv("FURBOORU_API_KEY", "").strip()
SAFE_FILTER_ID = os.getenv("SAFE_FILTER_ID", "").strip()
NSFW_FILTER_ID = os.getenv("NSFW_FILTER_ID", "").strip()

USER_AGENT = os.getenv("USER_AGENT", "giffer-bot/3.3 (by @giffer@bronyfurry.com)").strip()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

# Rate-limit / anti-spam
USER_COOLDOWN_SEC = int(os.getenv("USER_COOLDOWN_SEC", "20"))
GLOBAL_RATE_PER_SEC = float(os.getenv("GLOBAL_RATE_PER_SEC", "1.0"))
GLOBAL_BURST = int(os.getenv("GLOBAL_BURST", "3"))

# Network hardening
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "20"))
SOCKET_DEFAULT_TIMEOUT = float(os.getenv("SOCKET_DEFAULT_TIMEOUT", "25"))

# Watchdog for Mastodon calls
MASTO_CALL_TIMEOUT = float(os.getenv("MASTO_CALL_TIMEOUT", "25"))

# Media download safety
MAX_GIF_BYTES = int(os.getenv("MAX_GIF_BYTES", str(25 * 1024 * 1024)))  # 25MB
DOWNLOAD_TIMEOUT = float(os.getenv("DOWNLOAD_TIMEOUT", "40"))

# Wait for Mastodon media processing
MEDIA_PROCESS_MAX_WAIT = float(os.getenv("MEDIA_PROCESS_MAX_WAIT", "60"))

NSFW_VISIBILITY = os.getenv("NSFW_VISIBILITY", "public").strip()  # public/unlisted/private/direct

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "giffer.log").strip() or "giffer.log"

# State (for "do not reply twice")
STATE_FILE = os.getenv("STATE_FILE", "giffer_state.json").strip() or "giffer_state.json"
PROCESSED_CACHE_MAX = int(os.getenv("PROCESSED_CACHE_MAX", "800"))  # how many status_ids to remember


# =======================
# LOGGING
# =======================

logger = logging.getLogger("giffer")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger.handlers.clear()

fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

ch = logging.StreamHandler()
ch.setFormatter(fmt)
logger.addHandler(ch)

fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
fh.setFormatter(fmt)
logger.addHandler(fh)


# =======================
# GLOBAL NETWORK SETTINGS
# =======================

socket.setdefaulttimeout(SOCKET_DEFAULT_TIMEOUT)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    if Retry is not None:
        retry = Retry(
            total=3,
            connect=3,
            read=2,
            status=2,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    else:
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)

    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


http = make_session()

_executor = ThreadPoolExecutor(max_workers=4)
T = TypeVar("T")


def run_with_timeout(fn: Callable[[], T], timeout: float, what: str) -> Optional[T]:
    fut = _executor.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except FuturesTimeoutError:
        logger.error("Timeout: %s (>%ss). Continuing.", what, timeout)
        return None
    except Exception as e:
        logger.error("Error in %s: %s", what, e)
        return None


# =======================
# STATE (persist last_seen + processed status_ids)
# =======================

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"last_seen_notif_id": None, "processed_status_ids": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "last_seen_notif_id" not in data:
            data["last_seen_notif_id"] = None
        if "processed_status_ids" not in data or not isinstance(data["processed_status_ids"], list):
            data["processed_status_ids"] = []
        return data
    except Exception as e:
        logger.warning("Failed to read state file (%s). Starting fresh.", e)
        return {"last_seen_notif_id": None, "processed_status_ids": []}

def save_state(state: Dict[str, Any]) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.warning("Failed to write state file: %s", e)

def remember_processed(state: Dict[str, Any], status_id: int) -> None:
    arr = state.get("processed_status_ids", [])
    arr.append(int(status_id))
    # keep last N
    if len(arr) > PROCESSED_CACHE_MAX:
        arr = arr[-PROCESSED_CACHE_MAX:]
    state["processed_status_ids"] = arr

def is_processed(state: Dict[str, Any], status_id: int) -> bool:
    try:
        return int(status_id) in set(state.get("processed_status_ids", []))
    except Exception:
        return False


# =======================
# RATE LIMIT HELPERS
# =======================

_user_last: Dict[str, float] = {}
_global_tokens = float(GLOBAL_BURST)
_global_last = time.monotonic()


def user_allowed(acct: str) -> bool:
    now = time.time()
    last = _user_last.get(acct, 0.0)
    if now - last < USER_COOLDOWN_SEC:
        logger.info("Cooldown hit for user=%s (wait %.1fs)", acct, USER_COOLDOWN_SEC - (now - last))
        return False
    _user_last[acct] = now
    return True


def global_wait_if_needed() -> None:
    global _global_tokens, _global_last
    now = time.monotonic()
    elapsed = now - _global_last
    _global_last = now

    _global_tokens = min(float(GLOBAL_BURST), _global_tokens + elapsed * GLOBAL_RATE_PER_SEC)

    if _global_tokens >= 1.0:
        _global_tokens -= 1.0
        return

    need = (1.0 - _global_tokens) / max(GLOBAL_RATE_PER_SEC, 0.001)
    logger.info("Global rate-limit wait %.2fs", need)
    time.sleep(max(0.0, need))
    _global_tokens = 0.0


# =======================
# TEXT HELPERS
# =======================

def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def parse_query(content_html: str) -> Tuple[str, bool]:
    text = strip_html(content_html).lower()
    text = re.sub(r"@\s*[a-z0-9_]+(?:@[a-z0-9\.\-]+)?", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()

    is_nsfw = bool(re.search(r"\bnsfw\b", text))
    text = re.sub(r"\bnsfw\b", " ", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text, is_nsfw


def split_tags(query: str) -> List[str]:
    q = (query or "").strip()
    if not q:
        return []
    q = q.replace(",", " ")
    parts = re.findall(r'"([^"]+)"|(\S+)', q)
    tokens = []
    for a, b in parts:
        t = (a or b).strip()
        if t:
            tokens.append(t)
    tokens = [t for t in tokens if t.lower() not in {"random", "rnd"}]
    return tokens


def safe_visibility(v: str) -> str:
    v = (v or "public").strip().lower()
    return v if v in {"public", "unlisted", "private", "direct"} else "public"


def make_alt_text(img: Dict[str, Any], query: str, is_nsfw: bool) -> str:
    tags = img.get("tags", [])
    if isinstance(tags, list):
        raw = [str(t).strip() for t in tags]
    elif isinstance(tags, str):
        raw = [t.strip() for t in tags.split(",")]
    else:
        raw = []

    block = {"safe", "questionable", "explicit", "nsfw", "sfw", "animated", "gif"}
    cleaned = []
    for t in raw:
        if not t:
            continue
        t2 = t.lower().strip()
        if t2 in block:
            continue
        cleaned.append(t2.replace("_", " "))

    seen = set()
    uniq = []
    for t in cleaned:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)

    prefix = "NSFW animated GIF" if is_nsfw else "Animated GIF"
    if uniq:
        return (f"{prefix}: " + ", ".join(uniq[:20]))[:900]
    if query:
        return f"{prefix} (from query): {query[:900]}"
    return prefix


def source_link(img: Dict[str, Any]) -> str:
    vu = img.get("view_url")
    if isinstance(vu, str) and vu.startswith("http"):
        return vu
    img_id = img.get("id")
    if img_id is not None:
        return f"{FURBOORU_BASE_URL}/images/{img_id}"
    return FURBOORU_BASE_URL


# =======================
# FURBOORU
# =======================

def furbooru_search_gif(query: str, nsfw: bool) -> Optional[Dict[str, Any]]:
    global_wait_if_needed()

    url = f"{FURBOORU_BASE_URL}/api/v1/json/search/images"
    base_tags = ["animated", "gif"] if nsfw else ["safe", "animated", "gif"]
    user_tags = split_tags(query)
    all_tags = user_tags + base_tags
    q = ", ".join(all_tags) if all_tags else ", ".join(base_tags)

    params: Dict[str, Any] = {"q": q, "per_page": 50, "page": 1}
    if FURBOORU_API_KEY:
        params["key"] = FURBOORU_API_KEY
    if nsfw and NSFW_FILTER_ID:
        params["filter_id"] = NSFW_FILTER_ID
    if (not nsfw) and SAFE_FILTER_ID:
        params["filter_id"] = SAFE_FILTER_ID

    backoff = 1.0
    for attempt in range(4):
        logger.info("Furbooru search attempt=%d q=%s", attempt + 1, q)
        try:
            r = http.get(url, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        except requests.RequestException as e:
            logger.error("Furbooru request error: %s", e)
            return None

        if r.status_code == 429:
            logger.warning("Furbooru 429 Too Many Requests. Backoff %.1fs", backoff)
            time.sleep(backoff)
            backoff *= 2
            continue

        if r.status_code >= 400:
            logger.error("Furbooru HTTP %s: %r", r.status_code, (r.text or "")[:200])
            return None

        try:
            data = r.json()
        except Exception as e:
            logger.error("Furbooru JSON parse error: %s", e)
            return None

        images = data.get("images", [])
        candidates = []
        for img in images:
            if img.get("format") != "gif":
                continue
            if not img.get("thumbnails_generated", False):
                continue
            reps = img.get("representations") or {}
            if not any(reps.get(k) for k in ("full", "large", "medium", "small", "thumb")):
                continue
            candidates.append(img)

        logger.info("Furbooru found=%d candidates=%d", len(images), len(candidates))
        return random.choice(candidates) if candidates else None

    return None


def representation_candidates(img: Dict[str, Any]) -> List[str]:
    reps = img.get("representations") or {}
    keys = ["full", "large", "medium", "small", "thumb"]
    out: List[str] = []
    for k in keys:
        u = reps.get(k)
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    return out


def download_bytes(url: str, max_bytes: int) -> Optional[bytes]:
    logger.info("Downloading %s", url)
    try:
        with http.get(url, timeout=(CONNECT_TIMEOUT, DOWNLOAD_TIMEOUT), stream=True) as r:
            if r.status_code >= 400:
                logger.error("Download HTTP %s", r.status_code)
                return None
            total = 0
            chunks = []
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"File too large (> {max_bytes} bytes)")
                chunks.append(chunk)
            logger.info("Downloaded bytes=%d", total)
            return b"".join(chunks)
    except ValueError:
        raise
    except requests.RequestException as e:
        logger.error("Download error: %s", e)
        return None


# =======================
# GIF -> MP4 (ffmpeg)
# =======================

def find_ffmpeg_exe() -> Optional[str]:
    for cmd in ("ffmpeg", "ffmpeg.exe"):
        try:
            p = subprocess.run([cmd, "-version"], capture_output=True, text=True)
            if p.returncode == 0:
                return cmd
        except Exception:
            pass
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


def gif_bytes_to_mp4(gif_bytes: bytes) -> Optional[bytes]:
    ffmpeg = find_ffmpeg_exe()
    if not ffmpeg:
        logger.error("FFmpeg not found. Install ffmpeg or `pip install imageio-ffmpeg`.")
        return None

    with tempfile.TemporaryDirectory() as td:
        in_gif = os.path.join(td, "in.gif")
        out_mp4 = os.path.join(td, "out.mp4")

        with open(in_gif, "wb") as f:
            f.write(gif_bytes)

        cmd = [
            ffmpeg, "-y",
            "-i", in_gif,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "28",
            "-an",
            out_mp4
        ]

        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            logger.error("FFmpeg execution error: %s", e)
            return None

        if p.returncode != 0:
            logger.error("FFmpeg failed: %s", (p.stderr or "")[-400:])
            return None

        try:
            with open(out_mp4, "rb") as f:
                mp4 = f.read()
            logger.info("Converted GIF->MP4 bytes=%d", len(mp4))
            return mp4
        except Exception as e:
            logger.error("Reading MP4 failed: %s", e)
            return None


# =======================
# MASTODON
# =======================

def init_mastodon():
    if not MASTODON_ACCESS_TOKEN:
        raise SystemExit("MASTODON_ACCESS_TOKEN is empty. Put it into config.env or env vars.")
    from mastodon import Mastodon
    return Mastodon(
        access_token=MASTODON_ACCESS_TOKEN,
        api_base_url=MASTODON_BASE_URL,
        user_agent=USER_AGENT,
        request_timeout=max(CONNECT_TIMEOUT + READ_TIMEOUT, 10.0),
    )


def upload_media(mastodon, data: bytes, mime: str, alt_text: str) -> Optional[int]:
    bio = io.BytesIO(data)
    bio.name = "giffer." + ("mp4" if mime == "video/mp4" else "gif")
    logger.info("Uploading media mime=%s (alt_len=%d)", mime, len(alt_text))

    def _call():
        return mastodon.media_post(media_file=bio, mime_type=mime, description=alt_text)

    media = run_with_timeout(_call, MASTO_CALL_TIMEOUT, f"mastodon.media_post({mime})")
    if not media:
        return None
    return int(media["id"])


def wait_media_ready(mastodon, media_id: int, max_wait: float) -> bool:
    start = time.time()
    delay = 0.6
    while time.time() - start < max_wait:
        att = run_with_timeout(lambda: mastodon.media(media_id), MASTO_CALL_TIMEOUT, "mastodon.media(get)")
        if att and att.get("url"):
            return True
        time.sleep(delay)
        delay = min(delay * 1.4, 3.0)
    return False


def post_reply_safe(mastodon, status_id: int, text: str, visibility: str) -> None:
    def _call():
        return mastodon.status_post(text, in_reply_to_id=status_id, visibility=visibility)
    run_with_timeout(_call, MASTO_CALL_TIMEOUT, "mastodon.status_post(reply)")


def post_status_with_media(mastodon, status_id: int, text: str, media_id: int,
                          visibility: str, nsfw: bool) -> bool:
    if nsfw:
        def _call():
            return mastodon.status_post(
                text,
                in_reply_to_id=status_id,
                media_ids=[media_id],
                sensitive=True,
                spoiler_text="NSFW",
                visibility=visibility,
            )
        res = run_with_timeout(_call, MASTO_CALL_TIMEOUT, "mastodon.status_post(nsfw)")
        return res is not None
    else:
        def _call():
            return mastodon.status_post(
                text,
                in_reply_to_id=status_id,
                media_ids=[media_id],
                visibility=visibility,
            )
        res = run_with_timeout(_call, MASTO_CALL_TIMEOUT, "mastodon.status_post(sfw)")
        return res is not None


def is_mastodon_422_unsupported(err_msg: str) -> bool:
    s = (err_msg or "").lower()
    return (" 422" in s or "unprocessable" in s) and ("not supported" in s or "gif" in s or "supported" in s)


def upload_gif_then_mp4_fallback(mastodon, img: Dict[str, Any], alt: str) -> Tuple[Optional[int], str]:
    urls = representation_candidates(img)
    if not urls:
        return None, ""

    last_err = ""

    # GIF attempts (big -> small)
    for u in urls:
        try:
            gif_bytes = download_bytes(u, MAX_GIF_BYTES)
            if not gif_bytes:
                last_err = "download failed"
                continue
            mid = upload_media(mastodon, gif_bytes, "image/gif", alt)
            if mid is not None:
                return mid, "image/gif"
            last_err = "upload timeout/none"
        except Exception as e:
            last_err = str(e)
            if is_mastodon_422_unsupported(last_err):
                logger.warning("GIF rejected (422) for %s: %s. Trying smaller...", u, last_err)
                continue
            logger.error("GIF upload failed hard: %s", last_err)
            break

    # MP4 fallback (use smallest downloadable gif)
    logger.info("Trying MP4 fallback...")
    gif_bytes = None
    for u in reversed(urls):
        b = download_bytes(u, MAX_GIF_BYTES)
        if b:
            gif_bytes = b
            break
    if not gif_bytes:
        logger.error("Cannot download any GIF for MP4 conversion. Last error: %s", last_err)
        return None, ""

    mp4 = gif_bytes_to_mp4(gif_bytes)
    if not mp4:
        return None, ""

    mid = upload_media(mastodon, mp4, "video/mp4", alt)
    if mid is None:
        return None, ""
    return mid, "video/mp4"


# =======================
# MAIN LOOP
# =======================

def reply_text(prefix: str, query: str, src: str, used_mime: str) -> str:
    q = query if query else "random"
    note = "\n(конвертировано в MP4 из-за ограничений инстанса)" if used_mime == "video/mp4" else ""
    return f"{prefix} по запросу: `{q}`{note}\nОриг: {src}"


def main():
    state = load_state()
    last_seen_id = state.get("last_seen_notif_id", None)

    # Convert to int if it's stored as string
    try:
        if last_seen_id is not None:
            last_seen_id = int(last_seen_id)
    except Exception:
        last_seen_id = None

    processed_set = set(int(x) for x in state.get("processed_status_ids", []) if isinstance(x, int) or str(x).isdigit())
    # normalize back into state list
    state["processed_status_ids"] = list(processed_set)[-PROCESSED_CACHE_MAX:]
    save_state(state)

    mastodon = init_mastodon()
    from mastodon import MastodonError

    logger.info("Starting giffer bot on %s (Furbooru: %s)", MASTODON_BASE_URL, FURBOORU_BASE_URL)
    logger.info("Loaded state: last_seen_notif_id=%s processed_cache=%d", last_seen_id, len(state["processed_status_ids"]))

    while True:
        logger.info("Polling mentions... since_id=%s", last_seen_id)

        def _fetch():
            return mastodon.notifications(types=["mention"], since_id=last_seen_id)

        notifs = run_with_timeout(_fetch, MASTO_CALL_TIMEOUT, "mastodon.notifications(mention)")
        if notifs is None:
            time.sleep(CHECK_INTERVAL)
            continue

        logger.info("Got %d mention notifications", len(notifs))

        for notif in reversed(notifs):
            notif_id = notif.get("id")
            if notif_id is not None:
                try:
                    last_seen_id = int(notif_id)
                    state["last_seen_notif_id"] = last_seen_id
                    save_state(state)  # <- important: persist immediately
                except Exception:
                    pass

            acct = notif.get("account", {}).get("acct", "unknown")
            status = notif.get("status") or {}
            status_id = status.get("id")
            if not status_id:
                continue

            try:
                status_id_int = int(status_id)
            except Exception:
                continue

            # ---- NEW: don't reply twice ----
            if status_id_int in processed_set:
                logger.info("Skipping already processed status_id=%s", status_id_int)
                continue

            # mark as processed early to prevent double-processing if we crash mid-way
            processed_set.add(status_id_int)
            remember_processed(state, status_id_int)
            save_state(state)

            if not user_allowed(acct):
                continue

            query, is_nsfw = parse_query(status.get("content", ""))
            logger.info("Mention from=%s nsfw=%s query=%r status_id=%s", acct, is_nsfw, query, status_id_int)

            try:
                img = furbooru_search_gif(query, is_nsfw)
                if not img:
                    post_reply_safe(
                        mastodon,
                        status_id_int,
                        f"Ничего не нашёл по запросу: `{query or 'random'}` 😿",
                        safe_visibility(status.get("visibility", "public")),
                    )
                    continue

                src = source_link(img)
                alt = make_alt_text(img, query, is_nsfw)

                media_id, used_mime = upload_gif_then_mp4_fallback(mastodon, img, alt)
                if not media_id:
                    post_reply_safe(
                        mastodon,
                        status_id_int,
                        "Не смог загрузить (инстанс отклоняет GIF/видео или проблемы сети). Попробуй другой запрос 🙏",
                        safe_visibility(status.get("visibility", "public")),
                    )
                    continue

                logger.info("Uploaded media_id=%s mime=%s. Waiting processing...", media_id, used_mime)
                if not wait_media_ready(mastodon, media_id, max_wait=MEDIA_PROCESS_MAX_WAIT):
                    post_reply_safe(
                        mastodon,
                        status_id_int,
                        "Медиа загрузилось, но Mastodon слишком долго его обрабатывает. Попробуй ещё раз через минуту 🙏",
                        safe_visibility(status.get("visibility", "public")),
                    )
                    continue

                visibility = safe_visibility(NSFW_VISIBILITY if is_nsfw else status.get("visibility", "public"))
                text = reply_text("NSFW" if is_nsfw else "GIF", query, src, used_mime)

                posted = post_status_with_media(mastodon, status_id_int, text, media_id, visibility, nsfw=is_nsfw)
                if posted:
                    logger.info("Posted successfully for user=%s", acct)
                else:
                    logger.error("Failed to post status for user=%s (status_id=%s)", acct, status_id_int)

            except ValueError as e:
                post_reply_safe(
                    mastodon,
                    status_id_int,
                    f"Не могу загрузить: {e}",
                    safe_visibility(status.get("visibility", "public")),
                )
            except requests.RequestException as e:
                post_reply_safe(
                    mastodon,
                    status_id_int,
                    f"Ошибка сети: {e}",
                    safe_visibility(status.get("visibility", "public")),
                )
            except MastodonError as e:
                logger.error("Mastodon error: %s", e)
            except Exception as e:
                logger.exception("Unexpected error: %s", e)
                post_reply_safe(
                    mastodon,
                    status_id_int,
                    f"Неожиданная ошибка: {e}",
                    safe_visibility(status.get("visibility", "public")),
                )

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
