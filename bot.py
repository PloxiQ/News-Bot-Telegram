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
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
TEST_RUN = os.environ.get("TEST_RUN", "false").lower() == "true"
TG_ADMIN_ID = os.environ.get("TG_ADMIN_ID", "")

BRAND = "PloxiQ News"
CHANNEL_URL = "https://t.me/PloxiQNews"
CHANNEL_HANDLE = "t.me/PloxiQNews"

MAX_PER_RUN = 1
MAX_AGE_HOURS = 12
STATE_FILE = "posted.json"
CUSTOM_FILE = "custom_news.txt"

GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
CAPTION_LIMIT = 1000
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 45 * 1024 * 1024

FONT_DIR = "assets"
FONT_PATH = os.path.join(FONT_DIR, "Montserrat.ttf")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf"
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

NO_ARTICLE_TOPICS = {"github", "новости", "reddit", "custom", "мемы", "thought"}
TOPIC_META = {
    "игры": ("🎮", "ИГРОВЫЕ НОВОСТИ", "#игры", "Читать источник"),
    "нейросети": ("🤖", "НЕЙРОСЕТИ", "#нейросети", "Читать источник"),
    "github": ("💻", "ТРЕНДЫ GITHUB", "#github", "Открыть репозиторий"),
    "reddit": ("🔥", "С REDDIT", "#reddit", "Источник"),
    "мемы": ("😂", "МЕМ", "#мемы", None),
    "новости": ("📰", "НОВОСТИ", "#новости", "Источник"),
    "custom": ("🔥", "ЭКСКЛЮЗИВ", "#эксклюзив", None),
    "thought": ("💭", "МЫСЛИ ВСЛУХ", "#мысли", None),
}

# Стили начала поста: один выбирается случайно при каждой генерации
STYLES = [
    "С интригой (Представь, что...)",
    "С фактом (Оказывается,...)",
    "С вопросом (А вы знали, что...)",
    "С шуткой (Срочно в номер:...)",
    "Сразу к делу (без вступления, просто суть)",
]

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


def media_ext(url):
    return os.path.splitext(urlparse(url).path)[1].lower()


def is_direct_video(url):
    return bool(url) and media_ext(url) in (".mp4", ".webm", ".mov")


def download(url, max_bytes, want_video=False):
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
        except Exception:
            pass

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
        except Exception:
            pass

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
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1280, 1280))
        w, h = img.size
        base = img.convert("RGBA")

        grad_h = max(60, int(h * 0.28))
        grad = Image.new("L", (1, grad_h))
        grad.putdata([int(185 * (i / (grad_h - 1)) ** 1.7) for i in range(grad_h)])
        grad = grad.resize((w, grad_h))
        shade = Image.new("RGBA", (w, grad_h), (6, 8, 16, 255))
        shade.putalpha(grad)
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        overlay.paste(shade, (0, h - grad_h))

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
        td.rounded_rectangle([margin, top, margin + bar_w, bottom], radius=bar_w // 2, fill=(255, 64, 75, 240))

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

        handle_w = td.textlength(CHANNEL_HANDLE, font=small)
        if w - margin - handle_w > x_end + margin:
            td.text((w - margin, baseline), CHANNEL_HANDLE, font=small, fill=(255, 255, 255, 150), anchor="rs")

        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(max(2, font_size * 0.07)))
        out = Image.alpha_composite(base, overlay)
        out = Image.alpha_composite(out, shadow_layer)
        out = Image.alpha_composite(out, text_layer).convert("RGB")

        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()
    except Exception as e:
        print("Watermark failed:", e)
        try:
            Image.open(io.BytesIO(image_bytes)).verify()
            return image_bytes
        except Exception:
            return None


def make_fallback_cover():
    w, h = 1280, 720
    grad = Image.linear_gradient("L").resize((w, h))
    img = ImageOps.colorize(grad, black=(10, 12, 28), white=(70, 16, 42)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def fetch_pixabay_image(query):
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get("https://pixabay.com/api/", params={"key": PIXABAY_KEY, "q": query[:100], "image_type": "photo", "per_page": 5, "safesearch": "true", "order": "popular"}, timeout=10)
        r.raise_for_status()
        hits = r.json().get("hits") or []
        urls = [h.get("largeImageURL") for h in hits if h.get("largeImageURL")]
        return random.choice(urls) if urls else None
    except Exception:
        return None


# ======================= GROQ =======================
def groq_json(prompt, temperature=0.6):
    if not GROQ_KEY:
        return None
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    for model in GROQ_MODELS:
        payload = {"model": model, "temperature": temperature, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
        for attempt in range(2):
            try:
                r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=60)
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


# ======================= ЛОГИКА =======================
def get_custom_news():
    """Забирает сообщения от админа из Telegram."""
    if not TG_ADMIN_ID:
        return None
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        updates = r.json().get("result", [])

        for update in reversed(updates):
            message = update.get("message")
            if not message:
                continue
            from_id = str(message.get("from", {}).get("id"))
            if from_id != TG_ADMIN_ID:
                continue

            text = message.get("text") or message.get("caption")
            if not text or text.startswith("/") or len(text) < 20:
                continue

            update_id = update.get("update_id")
            seen_updates = load_state()
            if f"tg_update_{update_id}" in seen_updates:
                continue

            media_url = None
            if "photo" in message:
                file_id = message["photo"][-1]["file_id"]
                file_info_url = f"https://api.telegram.org/bot{TG_TOKEN}/getFile?file_id={file_id}"
                file_info = requests.get(file_info_url, timeout=15).json()
                file_path = file_info.get("result", {}).get("file_path")
                if file_path:
                    media_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}"

            return {
                "id": f"tg_update_{update_id}",
                "link": CHANNEL_URL,
                "title": text[:50] + "...",
                "summary": text,
                "time": time.time(),
                "topic": "custom",
                "media": media_url
            }
    except Exception as e:
        print("getUpdates failed:", e)
    return None


def get_file_news():
    """Новость из custom_news.txt (необязательно). Один и тот же текст повторно не постится."""
    if not os.path.exists(CUSTOM_FILE):
        return None
    with open(CUSTOM_FILE, encoding="utf-8") as f:
        text = f.read().strip()
    if len(text) < 20:
        return None
    news_id = "file_" + hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
    if news_id in set(load_state()):
        return None
    return {
        "id": news_id,
        "link": CHANNEL_URL,
        "title": text[:50] + "...",
        "summary": text,
        "time": time.time(),
        "topic": "custom",
        "media": None,
    }


def rewrite(item, article_text):
    if not GROQ_KEY:
        return None
    source_text = article_text or item["summary"]
    chosen_style = random.choice(STYLES)
    print("Выбран стиль начала:", chosen_style)

    prompt = (
        "Ты — крутой автор Telegram-канала про игры и нейросети. Твой стиль — живой, неформальный язык, сарказм и личное мнение. "
        "Ты не копируешь новости, ты обсуждаешь их с друзьями.\n"
        f"Тема: {item['topic']}\n"
        f"Заголовок новости: {item['title']}\n"
        f"Исходный текст: {source_text}\n\n"
        "ПРАВИЛА:\n"
        "1. Никаких цитат. Перескажи суть своими словами, по-русски.\n"
        "2. title — цепляющий заголовок БЕЗ тегов. Можно кликбейт, но без вранья.\n"
        f"3. body — 2-4 предложения. Стиль начала поста: {chosen_style}. "
        "Следуй этому стилю и вплети суть новости в текст. Фраза в скобках — лишь подсказка, "
        "формулировку можно менять. Можно использовать теги <b></b> и <i></i> для акцентов.\n"
        "4. quote — короткая цитата или вывод (до 120 символов). Обычный текст.\n"
        "5. joke — твой личный комментарий, мем или сарказм по поводу новости (до 150 символов). Обычный текст.\n"
        "6. image_prompt — 2-3 слова на английском для поиска фото.\n"
        "7. hype — число от 1 до 10 (насколько новость хайповая).\n"
        "8. verdict — короткий вердикт (например: 'Брать', 'Ждать скидку', 'Пропустить', 'Топчик').\n"
        "ОЦЕНКА: ты строгий критик, не завышай баллы.\n"
        "- Реально крутая новость (крупный анонс, утечка, полезный инструмент) — hype 8-10, verdict: 'Брать', 'Топчик', 'Срочно качаем'.\n"
        "- Средняя новость (обычное обновление, слух) — hype 4-7, verdict: 'Ждать скидку', 'На любителя', 'Норм'.\n"
        "- Скучная/мусорная (реклама, скучный патч, вода) — hype 1-3, verdict: 'Пропустить', 'Мимо', 'Скука'.\n"
        "НИКОГДА не начинай пост с фразы 'Кто бы мог подумать'. Используй разные вступления. "
        "Каждый пост должен быть уникальным по структуре.\n"
        "Верни СТРОГО JSON:\n"
        '{"emoji": "1 эмодзи", "title": "...", "body": "...", "quote": "...", "joke": "...", "image_prompt": "...", "hype": 5, "verdict": "..."}'
    )

    data = groq_json(prompt)
    if isinstance(data, dict) and data.get("title") and data.get("body"):
        return data
    return None


def sanitize_html(text, max_chars=None):
    text = re.sub(r"<\s*br\s*/?>", "\n", text or "", flags=re.I)
    out, stack, count = [], [], 0
    for m in re.finditer(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)[^>]*>|<[!?][^>]*>|[^<]+|<", text, re.S):
        tok = m.group(0)
        if m.group(2):
            name = m.group(2).lower()
            name = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}.get(name, name)
            if name not in {"b", "i", "u", "s", "code", "blockquote"}:
                continue
            if m.group(1):
                if name in stack:
                    while stack:
                        top = stack.pop()
                        out.append(f"</{top}>")
                        if top == name:
                            break
            else:
                stack.append(name)
                out.append(f"<{name}>")
        elif tok.startswith("<!") or tok.startswith("<?"):
            continue
        else:
            plain = "<" if tok == "<" else html.unescape(tok)
            if max_chars is not None and count + len(plain) > max_chars:
                plain = plain[: max(0, max_chars - count)].rstrip() + "…"
                out.append(html.escape(plain, quote=False))
                break
            out.append(html.escape(plain, quote=False))
            count += len(plain)
    while stack:
        out.append(f"</{stack.pop()}>")
    result = "".join(out)
    result = re.sub(r"<(b|i|u|s|code|blockquote)>\s*</\1>", "", result)
    return result.strip()


def plain_field(value, limit):
    text = clean(str(value or ""))
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return html.escape(text, quote=False)


def build_post(item, ai):
    if not ai:
        return None
    default_icon, _, hashtag, link_label = TOPIC_META.get(item["topic"], TOPIC_META["игры"])

    icon = str(ai.get("emoji") or "").strip()
    if not icon or len(icon) > 4:
        icon = default_icon

    title = plain_field(ai.get("title"), 140)
    quote = plain_field(ai.get("quote"), 160)
    joke = plain_field(ai.get("joke"), 160)
    verdict = plain_field(ai.get("verdict"), 30)

    try:
        hype = int(ai.get("hype", 5))
        hype = max(1, min(10, hype))
    except Exception:
        hype = 5
    hype_bar = "▰" * hype + "▱" * (10 - hype)

    link = item.get("link", "")
    link_line = ""
    if link_label and link.startswith("http") and item["topic"] != "custom":
        link_line = f'🔗 <a href="{html.escape(link, quote=True)}">{link_label}</a>'
    footer = ("▬▬▬▬▬▬▬▬▬▬▬▬\n" f'⚡ <a href="{CHANNEL_URL}"><b>{BRAND}</b></a> · гейминг и нейросети')

    text = ""
    for body_limit in (700, 560, 440, 320, 220, 140):
        parts = [f"{icon} <b>{title}</b>", "", sanitize_html(ai.get("body", ""), body_limit)]

        if len(quote) > 3:
            parts += ["", f"<blockquote>{quote}</blockquote>"]

        if len(joke) > 3:
            parts += ["", f"💬 <i>{joke}</i>"]

        parts += ["", f"🔥 <b>Хайп:</b> {hype_bar}"]

        if len(verdict) > 1:
            parts += [f"💡 <b>Вердикт:</b> {verdict}"]

        if link_line:
            parts += ["", link_line]

        parts += ["", hashtag, "", footer]
        text = "\n".join(parts)
        if visible_len(text) <= CAPTION_LIMIT:
            break
    return text


def tg_call(method, data, files=None, timeout=90):
    err = "unknown"
    for _ in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", data=data, files=files, timeout=timeout)
        except requests.RequestException as e:
            err = str(e)
            time.sleep(3)
            continue
        if r.ok:
            return True, r.json().get("result", {})
        try:
            info = r.json()
        except ValueError:
            info = {}
        err = info.get("description", r.text[:200])
        if r.status_code == 429:
            time.sleep(min(int(info.get("parameters", {}).get("retry_after", 5)), 60))
            continue
        break
    print(f"TG {method} error:", err)
    return False, err


def send_with_fallback(method, text_key, html_text, extra=None, files=None):
    data = {"chat_id": CHAT_ID, text_key: html_text, "parse_mode": "HTML", **(extra or {})}
    ok, res = tg_call(method, data, files)
    if not ok and isinstance(res, str) and "parse entities" in res.lower():
        data[text_key] = strip_tags(html_text)
        data.pop("parse_mode")
        ok, res = tg_call(method, data, files)
    return ok, res


def image_candidates(site_image, query):
    if site_image:
        yield site_image
    px = fetch_pixabay_image(query)
    if px:
        yield px


def send_post(item, ai, video_url, image_url):
    text = build_post(item, ai)
    if not text:
        return None

    if video_url:
        try:
            data = download(video_url, MAX_VIDEO_BYTES, want_video=True)
            ok, res = send_with_fallback("sendVideo", "caption", text, {"supports_streaming": "true"}, {"video": ("video.mp4", data, "video/mp4")})
            if ok and isinstance(res, dict):
                return res.get("message_id")
        except Exception:
            pass

    query = str(ai.get("image_prompt") or "gaming setup")
    for url in image_candidates(image_url, query):
        try:
            raw_data = download(url, MAX_IMAGE_BYTES)
            processed_img = add_watermark(raw_data)
            if not processed_img:
                continue
            ok, res = send_with_fallback("sendPhoto", "caption", text, None, {"photo": ("image.jpg", processed_img, "image/jpeg")})
            if ok and isinstance(res, dict):
                return res.get("message_id")
        except Exception:
            pass

    try:
        wm = add_watermark(make_fallback_cover())
        ok, res = send_with_fallback("sendPhoto", "caption", text, None, {"photo": ("cover.jpg", wm, "image/jpeg")})
        if ok and isinstance(res, dict):
            return res.get("message_id")
    except Exception:
        pass

    ok, res = send_with_fallback("sendMessage", "text", text, {"disable_web_page_preview": "true"})
    if ok and isinstance(res, dict):
        return res.get("message_id")
    return None


def main():
    seen = load_state()

    # Только ручные новости: сначала личка, потом custom_news.txt. Автопарсинга нет.
    custom = get_custom_news() or get_file_news()
    if not custom:
        print("Нет новых новостей от админа. Выходим.")
        return

    article_text = custom["summary"]
    image_url = custom.get("media")
    video_url = None

    ai = rewrite(custom, article_text)
    if not ai:
        print("Не удалось сгенерировать пост.")
        return

    print("Публикуем пост...")
    msg_id = send_post(custom, ai, video_url, image_url)
    if msg_id:
        seen.append(custom["id"])
        save_state(seen)

        tg_call("setMessageReaction", {
            "chat_id": CHAT_ID,
            "message_id": msg_id,
            "reaction": json.dumps([{"type": "emoji", "emoji": "🔥"}])
        })
        print("Успешно опубликовано!")


if __name__ == "__main__":
    main()
