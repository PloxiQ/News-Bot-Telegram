import calendar
import html
import json
import os
import re
import time
import random
import base64
import urllib.parse
import io

import feedparser
import requests
from PIL import Image, ImageDraw, ImageFont

# === НАСТРОЙКИ ===
TG_TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHANNEL"]
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")

MAX_PER_RUN = 3      
MAX_AGE_HOURS = 12   
STATE_FILE = "posted.json"
CUSTOM_FILE = "custom_news.txt"

DEFAULT_IMAGE = "https://image.pollinations.ai/prompt/dark%20gaming%20setup%20neon%20red%20and%20blue?width=1080&height=720&nologo=true&model=flux"

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

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_state(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-1000:], f, ensure_ascii=False)

def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return calendar.timegm(t) if t else None

# === НАЛОЖЕНИЕ ВОДЯНОГО ЗНАКА ===
def add_watermark(image_bytes, text="PloxiQ News"):
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        width, height = img.size
        
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        try:
            font_url = "https://github.com/google/fonts/raw/main/ofl/roboto/Roboto-Bold.ttf"
            font_data = requests.get(font_url, timeout=10).content
            font = ImageFont.truetype(io.BytesIO(font_data), int(height * 0.06))
        except:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        padding = int(height * 0.02)
        x = (width - text_width) / 2
        y = height - text_height - (padding * 3)
        
        # Рисуем красную полупрозрачную плашку
        draw.rectangle([(0, y - padding), (width, height)], fill=(200, 20, 20, 180))
        
        # Рисуем текст с обводкой
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
        
        out = Image.alpha_composite(img, overlay).convert("RGB")
        out_bytes = io.BytesIO()
        out.save(out_bytes, format="JPEG", quality=95)
        return out_bytes.getvalue()
    except Exception as e:
        print("Watermark failed:", e)
        return image_bytes

def fetch_pixabay_image(query):
    if not PIXABAY_KEY: return None
    try:
        url = "https://pixabay.com/api/"
        params = {"key": PIXABAY_KEY, "q": query, "image_type": "photo", "per_page": 3, "safesearch": "true", "order": "popular"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("hits"):
            return random.choice(data["hits"]).get("largeImageURL")
    except Exception as ex:
        pass
    return None

def get_github_media(repo_full_name):
    try:
        url = f"https://api.github.com/repos/{repo_full_name}/readme"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200: return None
        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        images = re.findall(r'!\[[^\]]*\]\((https?://[^\s\)]+)\)', content)
        images += re.findall(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', content)
        for img in images:
            if img.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return img
    except Exception:
        pass
    return None

def get_custom_news():
    try:
        with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip()
            if len(text) > 20: 
                return {"id": f"custom_{time.time()}", "link": "https://t.me/PloxiQNews", "title": text[:50] + "...", "summary": text, "time": time.time(), "topic": "custom"}
    except FileNotFoundError:
        pass
    return None

def clear_custom_news():
    try:
        with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass

def generate_thought():
    prompt = (
        "Ты — автор крутого Telegram-канала про игры и нейросети. "
        "Напиши короткий пост (2-3 предложения), который не является новостью (вопрос к подписчикам или мысль). "
        "Верни строго JSON:\n"
        '{"emoji": "1 эмодзи", "title": "Короткий заголовок", "body": "Текст", "hashtags": [], "image_prompt": "Промпт"}'
    )
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "temperature": 0.8}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        r.raise_for_status()
        raw = re.sub(r"^```(?:json)?|```$", "", r.json()["choices"][0]["message"]["content"].strip(), flags=re.M).strip()
        return json.loads(raw)
    except Exception as ex:
        return None

def get_github_trending():
    try:
        url = "https://api.github.com/search/repositories"
        params = {"q": "topic:artificial-intelligence created:>2026-09-01", "sort": "stars", "order": "desc", "per_page": 2}
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception:
        return []

def fetch_from_telegram(channel_name):
    items = []
    try:
        url = f"https://t.me/s/{channel_name}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200: return items
        messages = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.S)
        links = re.findall(r'data-post="([^"]+)"', r.text)
        images = re.findall(r'background-image:url\(\'([^\']+)\'\)', r.text)

        for i in range(min(len(messages), len(links))):
            text = clean(messages[i])
            link = f"https://t.me/{links[i]}"
            if len(text) > 100: 
                items.append({"id": link, "link": link, "title": text[:80] + "...", "summary": text[:1500], "time": time.time(), "topic": "новости", "media": images[i] if i < len(images) else None})
    except Exception:
        pass
    return items

def collect():
    items = []
    custom = get_custom_news()
    if custom: items.append(custom)

    for topic, url in FEEDS:
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0")
            for e in feed.entries[:7]:
                link = e.get("link")
                title = clean(e.get("title"))
                if link and title:
                    media = None
                    if topic == "мемы":
                        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', e.get("summary", ""))
                        if img_match: media = img_match.group(1)
                    
                    items.append({"id": e.get("id") or link, "link": link, "title": title, "summary": clean(e.get("summary"))[:1500], "time": entry_time(e) or time.time(), "topic": topic, "media": media})
        except Exception:
            pass
            
    for repo in get_github_trending():
        items.append({"id": f"github_{repo['id']}", "link": repo["html_url"], "title": repo["name"], "summary": repo.get("description") or "Описание отсутствует", "time": time.time(), "topic": "github", "media": get_github_media(repo["full_name"])})
        
    for ch in TG_CHANNELS:
        items.extend(fetch_from_telegram(ch))
        
    return items

def fetch_article(url):
    if url == "https://t.me/PloxiQNews": return "", None, None
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        page = r.text
    except Exception:
        return "", None, None
    
    img = None
    m_img = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I) or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', page, re.I))
    if m_img: img = html.unescape(m_img.group(1))

    video = None
    m_vid = (re.search(r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)', page, re.I) or re.search(r'<meta[^>]+property=["\']og:video:url["\'][^>]+content=["\']([^"\']+)', page, re.I))
    if m_vid: video = html.unescape(m_vid.group(1))

    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I)
    text = " ".join(p for p in (clean(x) for x in paras) if len(p) > 60)
    return text[:4000], img, video

def rewrite(item, article_text):
    if not GROQ_KEY: return None
    source_text = article_text or item["summary"]
    
    prompt = (
        f"Ты — топовый SMM-редактор Telegram-канала про игры и нейросети. Твоя задача — делать сочные, короткие посты.\n"
        f"Заголовок новости: {item['title']}\n"
        f"Исходный текст: {source_text}\n\n"
        "ПРАВИЛА:\n"
        "1. НИКАКИХ ЦИТАТ ИЗ ИСХОДНОГО ТЕКСТА. Перескажи суть своими словами.\n"
        "2. Используй HTML-теги для форматирования: <b>жирный текст</b> для важных слов, <i>курсив</i> для шуток, <blockquote>цитата</blockquote> для выделения главной мысли.\n"
        "3. Текст (body) строго 2-4 предложения.\n"
        "4. Шутка (joke) короткая и понятная. Если новость серьезная — пустая строка.\n"
        "5. Если это мем (topic: мемы) — просто переведи шутку на русский и адаптируй.\n"
        "6. В image_prompt напиши 2-3 слова на английском для поиска фото.\n\n"
        "Верни ответ СТРОГО в формате JSON:\n"
        '{"emoji": "1 эмодзи", "title": "Заголовок (можно с HTML)", "body": "Текст с HTML-тегами", "joke": "Шутка с HTML-тегами", "image_prompt": "Промпт"}'
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "temperature": 0.6}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        r.raise_for_status()
        raw = re.sub(r"^```(?:json)?|```$", "", r.json()["choices"][0]["message"]["content"].strip(), flags=re.M).strip()
        data = json.loads(raw)
        if data.get("title") and data.get("body"): return data
        return None
    except Exception as ex:
        print("groq failed:", ex)
        return None

def build_post(item, ai):
    esc = html.escape
    
    if item["topic"] == "thought": category, default_icon, link = "💭 <b>МЫСЛИ ВСЛУХ</b>", "💭", ""
    elif item["topic"] == "custom": category, default_icon, link = "🔥 <b>ЭКСКЛЮЗИВ</b>", "🔥", "🔥 <b>Наш эксклюзив!</b>"
    elif item["topic"] == "мемы": category, default_icon, link = "😂 <b>МЕМ</b>", "😂", ""
    elif item["topic"] == "github": category, default_icon, link = "💻 <b>ТРЕНДЫ GITHUB</b>", "💻", f'<a href="{esc(item["link"], quote=True)}">Ссылка на репозиторий</a>'
    elif item["topic"] == "нейросети": category, default_icon, link = "🤖 <b>НЕЙРОСЕТИ</b>", "🤖", f'<a href="{esc(item["link"], quote=True)}">Читать источник</a>'
    else: category, default_icon, link = "🎮 <b>ИГРОВЫЕ НОВОСТИ</b>", "🎮", f'<a href="{esc(item["link"], quote=True)}">Читать источник</a>'
    
    if not ai: return None 

    icon = ai.get("emoji") or default_icon
    
    lines = [f"{icon} {category}", "", f"{ai['title']}", "", ai.get("body", "")]
    
    if ai.get("joke") and len(str(ai.get("joke"))) > 3: 
        lines += ["", f"😂 <i>{ai['joke']}</i>"]

    if link: lines += ["", link]
    
    # Улучшенная подпись
    lines += ["", "━━━━━━━━━━━━━━━━━━", "⚡️ <a href='https://t.me/PloxiQNews'><b>PloxiQ News</b></a> | Твой источник гейминга"]
    
    return "\n".join(lines)

def send_post(item, ai, media_url):
    text = build_post(item, ai)
    if not text: return False

    is_video = False
    if media_url and media_url.startswith("http"):
        if media_url.lower().endswith(('.mp4', '.webm', '.mov')):
            is_video = True
    else:
        query = ai.get("image_prompt", "gaming setup")
        media_url = fetch_pixabay_image(query)
        if not media_url:
            media_url = DEFAULT_IMAGE

    if len(text) > 1000: text = text[:990] + "..."

    try:
        media_data = requests.get(media_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).content
        
        if is_video:
            print("Отправляем видео (без вотермарки)...")
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo", data={"chat_id": CHAT_ID, "caption": text, "parse_mode": "HTML"}, files={"video": media_data}, timeout=60)
        else:
            print("Накладываем вотермарку и отправляем фото...")
            media_data_with_wm = add_watermark(media_data, "PloxiQ News")
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", data={"chat_id": CHAT_ID, "caption": text, "parse_mode": "HTML"}, files={"photo": media_data_with_wm}, timeout=30)
            
        if r.ok: return True
        else: print("TG error:", r.text)
    except Exception as e:
        print("Media download failed:", e)

    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=30)
    return r.ok

def main():
    seen = load_state()
    items = collect()
    
    fresh_others = [i for i in items if i["id"] not in seen and time.time() - i["time"] < MAX_AGE_HOURS * 3600 and i["topic"] != "custom"]
    random.shuffle(fresh_others)
    
    candidates = fresh_others[:15]
    if items and items[0]["topic"] == "custom":
        candidates.insert(0, items[0])
    
    posted_count = 0

    for item in candidates:
        if posted_count >= MAX_PER_RUN: 
            break

        if item["topic"] in ["github", "новости", "reddit", "custom", "мемы"]:
            article_text = item["summary"]
            site_media = item.get("media")
        else:
            article_text, site_img, site_vid = fetch_article(item["link"])
            site_media = site_vid if site_vid else site_img

        ai = rewrite(item, article_text)
        
        if not ai:
            continue

        is_test = os.environ.get("TEST_RUN", "false").lower() == "true"
        
        if is_test:
            print("🧪 Тестовый режим...")
        else:
            if posted_count == 0:
                delay = random.randint(0, 600)
            else:
                delay = random.randint(600, 2100)
            
            if delay > 0:
                time.sleep(delay)

        if send_post(item, ai, site_media):
            seen.append(item["id"])
            posted_count += 1
            save_state(seen)
            if item["topic"] == "custom":
                clear_custom_news()
        time.sleep(5)

    print(f"За этот час успешно опубликовано: {posted_count}")

if __name__ == "__main__":
    main()
