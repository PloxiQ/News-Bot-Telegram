#!/usr/bin/env python3
"""PloxiQ News — бот для Telegram-канала про игры и нейросети."""

import base64
import calendar
import hashlib
import html
import io
import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# ======================= НАСТРОЙКИ =======================
TG_TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHANNEL"]
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")  # необязательно, поднимает лимиты API
TEST_RUN = os.environ.get("TEST_RUN", "false").lower() == "true"

BRAND = "PloxiQ News"
CHANNEL_URL = "https://t.me/PloxiQNews"
CHANNEL_HANDLE = "t.me/PloxiQNews"

MAX_PER_RUN = 3
MAX_AGE_HOURS = 12
STATE_FILE = "posted.json"
CUSTOM_FILE = "custom_news.txt"

GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]  # вторая — запасная
CAPTION_LIMIT = 1000        # у Telegram лимит подписи 1024 символа
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 45 * 1024 * 1024  # бот может загрузить до 50 МБ

FONT_DIR = "assets"
FONT_PATH = os.path.join(FONT_DIR, "Montserrat.ttf")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf"
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

FEEDS = [
    ("игры", "https://stopgame.ru/rss/rss_news.xml"),
    ("игры", "https://dtf.ru/rss/games"),
    ("игры", "https://www.igromania.ru/rss/news.xml"),
    ("игры", "https://vgtimes.ru/rss/news/"),
    ("игры", "https://www.playground.ru/rss/news.xml"),
    ("игры", "https://www.pcgamer.com/rss/"),
    ("игры", "https://www.eurogamer.net/feed"),
    ("нейросети", "https://vc.ru/rss/tag/нейросети"),
    ("нейросети", "https://3dnews.ru/news/rss/"),
    ("нейросети", "https://www.ixbt.com/export/news.rss"),
    ("нейросети", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("reddit", "https://www.reddit.com/r/gaming+pcgaming+GTA6+artificial+OpenAI/hot/.rss"),
    ("мемы", "https://www.reddit.com/r/memes+ProgrammerHumor+gamingmemes/hot/.rss"),
]
TG_CHANNELS = ["dtf_ru", "igromania"]

# Темы, у которых текст и картинка берутся прямо из ленты (без загрузки статьи)
NO_ARTICLE_TOPICS = {"github", "новости", "reddit", "custom", "мемы"}

# emoji, подпись, хэштег, текст ссылки (None — без ссылки)
TOPIC_META = {
    "игры": ("🎮", "ИГРОВЫЕ НОВОСТИ", "#игры", "Читать источник"),
    "нейросети": ("🤖", "НЕЙРОСЕТИ", "#нейросети", "Читать источник"),
    "github": ("💻", "ТРЕНДЫ GITHUB", "#github", "Открыть репозиторий"),
    "reddit": ("🔥", "С REDDIT", "#reddit", "Источник"),
    "мемы": ("😂", "МЕМ", "#мемы", None),
    "новости": ("📰", "НОВОСТИ", "#новости", "Источник"),
    "custom": ("🔥", "ЭКСКЛЮЗИВ", "#эксклюзив", None),
}

UA = "Mozilla/5.0 (compatible; PloxiQBot/1.0; +https://t.me/PloxiQNews)"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})


# ======================= УТИЛИТЫ =======================
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_state(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-1000:], f, ensure_ascii=False)


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def visible_len(text):
    return len(strip_tags(text))


def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    try:
        return calendar.timegm(t) if t else None
    except Exception:
        return None


def media_ext(url):
    return os.path.splitext(urlparse(url).path)[1].lower()


def is_direct_video(url):
    return bool(url) and media_ext(url) in (".mp4", ".webm", ".mov")


def download(url, max_bytes, want_video=False):
    """Скачивает файл с ограничением размера. Бросает исключение при проблемах."""
    with session.get(url, timeout=(10, 40), stream=True) as r:
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "").lower()
        if want_video and not (ctype.startswith("video/") or "octet-stream" in ctype):
            raise ValueError(f"не видео: {ctype}")
        if int(r.headers.get("Content-Length") or 0) > max_bytes:
            raise ValueError("файл слишком большой")
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError("файл слишком большой")
        return bytes(buf)


# ======================= ВОДЯНОЙ ЗНАК =======================
_font_download_tried = False


def get_font(size):
    """Montserrat Bold (кэшируется в assets/), затем системные шрифты, затем дефолтный."""
    global _font_download_tried
    if not os.path.exists(FONT_PATH) and not _font_download_tried:
        _font_download_tried = True
        try:
            r = requests.get(FONT_URL, timeout=15)
            r.raise_for_status()
            if len(r.content) > 50_000:
                os.makedirs(FONT_DIR, exist_ok=True)
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print("Font download failed:", e)

    if os.path.exists(FONT_PATH):
        try:
            font = ImageFont.truetype(FONT_PATH, size)
            try:
                font.set_variation_by_name("Bold")
            except Exception:
                try:
                    font.set_variation_by_axes([700])
                except Exception:
                    pass
            return font
        except Exception as e:
            print("Montserrat load failed:", e)

    for path in FALLBACK_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def add_watermark(image_bytes, text=BRAND):
    """Мягкий тёмный градиент снизу + акцентная полоска + логотип-текст."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1280, 1280))
        w, h = img.size
        base = img.convert("RGBA")

        # --- градиент снизу ---
        grad_h = max(60, int(h * 0.28))
        grad = Image.new("L", (1, grad_h))
        grad.putdata([int(185 * (i / (grad_h - 1)) ** 1.7) for i in range(grad_h)])
        grad = grad.resize((w, grad_h))
        shade = Image.new("RGBA", (w, grad_h), (6, 8, 16, 255))
        shade.putalpha(grad)
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        overlay.paste(shade, (0, h - grad_h))

        # --- размеры ---
        font_size = max(18, int(w * 0.042))
        font = get_font(font_size)
        small = get_font(max(11, int(font_size * 0.5)))
        margin = int(w * 0.035)
        baseline = h - margin - int(font_size * 0.25)

        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        td = ImageDraw.Draw(text_layer)
        sd = ImageDraw.Draw(shadow_layer)

        bar_w = max(4, int(font_size * 0.14))
        bbox = td.textbbox((0, 0), text, font=font, anchor="ls")
        top, bottom = baseline + bbox[1], baseline + bbox[3]
        td.rounded_rectangle(
            [margin, top, margin + bar_w, bottom], radius=bar_w // 2, fill=(255, 64, 75, 240)
        )

        x = margin + bar_w + int(font_size * 0.4)
        parts = text.split(" ", 1)
        first, second = parts[0], parts[1] if len(parts) > 1 else ""
        space = td.textlength(" ", font=font)
        sd.text((x + 2, baseline + 2), first, font=font, fill=(0, 0, 0, 170), anchor="ls")
        td.text((x, baseline), first, font=font, fill=(255, 255, 255, 235), anchor="ls")
        x2 = x + td.textlength(first, font=font) + space
        if second:
            sd.text((x2 + 2, baseline + 2), second, font=font, fill=(0, 0, 0, 170), anchor="ls")
            td.text((x2, baseline), second, font=font, fill=(255, 70, 80, 235), anchor="ls")
            x_end = x2 + td.textlength(second, font=font)
        else:
            x_end = x2

        # --- ссылка справа (если помещается) ---
        handle_w = td.textlength(CHANNEL_HANDLE, font=small)
        if w - margin - handle_w > x_end + margin:
            td.text(
                (w - margin, baseline), CHANNEL_HANDLE, font=small,
                fill=(255, 255, 255, 150), anchor="rs",
            )

        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(max(2, font_size * 0.07)))
        out = Image.alpha_composite(base, overlay)
        out = Image.alpha_composite(out, shadow_layer)
        out = Image.alpha_composite(out, text_layer).convert("RGB")

        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()
    except Exception as e:
        print("Watermark failed:", e)
        # Если картинка битая — вернуть None, чтобы бот взял следующую
        try:
            Image.open(io.BytesIO(image_bytes)).verify()
            return image_bytes
        except Exception:
            return None


def make_fallback_cover():
    """Своя обложка, если ни одну картинку достать не удалось (не зависит от интернета)."""
    w, h = 1280, 720
    grad = Image.linear_gradient("L").resize((w, h))
    img = ImageOps.colorize(grad, black=(10, 12, 28), white=(70, 16, 42)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# ======================= ИСТОЧНИКИ =======================
def fetch_pixabay_image(query):
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_KEY, "q": query[:100], "image_type": "photo",
                "per_page": 5, "safesearch": "true", "order": "popular",
            },
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json().get("hits") or []
        urls = [h.get("largeImageURL") for h in hits if h.get("largeImageURL")]
        return random.choice(urls) if urls else None
    except Exception as e:
        print("Pixabay failed:", e)
        return None


def github_headers():
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def get_github_media(repo_full_name):
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo_full_name}/readme",
            headers=github_headers(), timeout=15,
        )
        if r.status_code != 200:
            return None
        content = base64.b64decode(r.json()["content"]).decode("utf-8", "ignore")
        urls = re.findall(r"!\[[^\]]*\]\((https?://[^\s\)]+)\)", content)
        urls += re.findall(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', content)
        for u in urls:
            low = u.lower()
            if "shields.io" in low or "badge" in low:
                continue
            if media_ext(u) in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                return u
    except Exception:
        pass
    return None


def get_github_trending():
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={
                "q": f"topic:artificial-intelligence created:>{since}",
                "sort": "stars", "order": "desc", "per_page": 2,
            },
            headers=github_headers(), timeout=15,
        )
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as e:
        print("GitHub trending failed:", e)
        return []


def get_custom_news():
    try:
        with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except FileNotFoundError:
        return None
    if len(text) <= 20:
        return None
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"custom_{digest}", "link": CHANNEL_URL, "title": text[:50] + "...",
        "summary": text, "time": time.time(), "topic": "custom", "media": None,
    }


def clear_custom_news():
    try:
        with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass


def fetch_from_telegram(channel_name):
    items = []
    try:
        r = session.get(f"https://t.me/s/{channel_name}", timeout=15)
        if r.status_code != 200:
            return items
        blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', r.text)
        for block in blocks:
            post = re.search(r'data-post="([^"]+)"', block)
            msg = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
            if not post or not msg:
                continue
            text = clean(msg.group(1))
            if len(text) <= 100:
                continue
            photo = re.search(
                r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", block
            )
            when = re.search(r'<time[^>]+datetime="([^"]+)"', block)
            ts = time.time()
            if when:
                try:
                    ts = datetime.fromisoformat(when.group(1)).timestamp()
                except ValueError:
                    pass
            link = f"https://t.me/{post.group(1)}"
            items.append({
                "id": link, "link": link, "title": text[:80] + "...", "summary": text[:1500],
                "time": ts, "topic": "новости", "media": photo.group(1) if photo else None,
            })
    except Exception as e:
        print(f"Telegram {channel_name} failed:", e)
    return items


def reddit_media(summary_html):
    m = re.search(r'href=["\'](https?://i\.redd\.it/[^"\']+)["\']', summary_html or "")
    if m:
        return html.unescape(m.group(1))
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary_html or "")
    return html.unescape(m.group(1)) if m else None


def collect():
    items = []
    custom = get_custom_news()
    if custom:
        items.append(custom)

    for topic, url in FEEDS:
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            for e in feed.entries[:7]:
                link, title = e.get("link"), clean(e.get("title"))
                if not (link and title):
                    continue
                raw_summary = e.get("summary", "")
                media = reddit_media(raw_summary) if topic in ("мемы", "reddit") else None
                items.append({
                    "id": e.get("id") or link, "link": link, "title": title,
                    "summary": clean(raw_summary)[:1500],
                    "time": entry_time(e) or time.time(), "topic": topic, "media": media,
                })
        except Exception as e:
            print(f"Feed failed {url}:", e)

    for repo in get_github_trending():
        items.append({
            "id": f"github_{repo['id']}", "link": repo["html_url"], "title": repo["name"],
            "summary": repo.get("description") or "Описание отсутствует",
            "time": time.time(), "topic": "github",
            "media": get_github_media(repo["full_name"]),
        })

    for ch in TG_CHANNELS:
        items.extend(fetch_from_telegram(ch))

    # убираем дубликаты по id
    unique, ids = [], set()
    for it in items:
        if it["id"] not in ids:
            ids.add(it["id"])
            unique.append(it)
    return unique


def meta_content(page, *props):
    for p in props:
        name = re.escape(p)
        for pat in (
            rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{name}["\']',
        ):
            m = re.search(pat, page, re.I)
            if m:
                return html.unescape(m.group(1)).strip()
    return None


def fetch_article(url):
    """Возвращает (текст, картинка, прямое_видео)."""
    try:
        r = session.get(url, timeout=12)
        r.raise_for_status()
        if r.encoding in (None, "ISO-8859-1"):
            r.encoding = r.apparent_encoding
        page = r.text
    except Exception:
        return "", None, None

    img = meta_content(page, "og:image", "og:image:url", "twitter:image")
    video = meta_content(page, "og:video:secure_url", "og:video", "og:video:url")
    if img:
        img = urljoin(url, img)
    if video:
        video = urljoin(url, video)
        if not is_direct_video(video):  # YouTube-эмбеды и плееры отправить нельзя
            video = None

    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I)
    text = " ".join(p for p in (clean(x) for x in paras) if len(p) > 60)
    return text[:4000], img, video


# ======================= GROQ =======================
def groq_json(prompt, temperature=0.6):
    if not GROQ_KEY:
        return None
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    for model in GROQ_MODELS:
        payload = {
            "model": model, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        for attempt in range(2):
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers, json=payload, timeout=60,
                )
                if r.status_code == 429:
                    time.sleep(8)
                    continue
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", raw, re.S)
                return json.loads(m.group(0)) if m else None
            except Exception as e:
                print(f"Groq {model} failed:", e)
                break
    return None


def rewrite(item, article_text):
    source_text = (article_text or item["summary"])[:3500]
    prompt = (
        "Ты — топовый SMM-редактор Telegram-канала про игры и нейросети. "
        "Делай сочные, короткие посты.\n"
        f"Тема: {item['topic']}\n"
        f"Заголовок новости: {item['title']}\n"
        f"Исходный текст: {source_text}\n\n"
        "ПРАВИЛА:\n"
        "1. Никаких цитат из исходника — перескажи суть своими словами, по-русски.\n"
        "2. title — короткий цепляющий заголовок, обычный текст БЕЗ тегов.\n"
        "3. body — 2-4 предложения. Можно использовать ТОЛЬКО теги <b></b> (1-3 раза "
        "для ключевых слов) и <i></i>. Никаких других тегов и markdown.\n"
        "4. quote — одна короткая мысль/вывод до 120 символов, обычный текст, "
        "не повторяющий body. Если нечего сказать — пустая строка.\n"
        "5. joke — короткая понятная шутка обычным текстом. Для серьёзных новостей — пустая строка.\n"
        "6. Если topic = мемы — переведи шутку на русский и адаптируй; quote оставь пустым.\n"
        "7. image_prompt — 2-3 слова на английском для поиска фото.\n\n"
        "Верни СТРОГО JSON:\n"
        '{"emoji": "1 эмодзи", "title": "...", "body": "...", "quote": "...", '
        '"joke": "...", "image_prompt": "..."}'
    )
    data = groq_json(prompt)
    i
