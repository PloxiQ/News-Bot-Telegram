import calendar
import html
import json
import os
import re
import time
import random
import base64
import urllib.parse

import feedparser
import requests

# === НАСТРОЙКИ ===
TG_TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHANNEL"]
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

MAX_PER_RUN = 3      # Сколько постов максимум за 1 запуск (раз в час)
MAX_AGE_HOURS = 12   # Берем только новости не старше 12 часов
STATE_FILE = "posted.json"

BASE_TAGS = {"игры": "#игры", "нейросети": "#нейросети", "github": "#github"}

# 🚀 ОБНОВЛЕННЫЕ ИСТОЧНИКИ (Убрали сложный Хабр)
FEEDS = [
    # Игры (Русские)
    ("игры", "https://stopgame.ru/rss/rss_news.xml"),
    ("игры", "https://dtf.ru/rss/games"),
    ("игры", "https://www.igromania.ru/rss/news.xml"),
    ("игры", "https://vgtimes.ru/rss/news/"),
    ("игры", "https://www.playground.ru/rss/news.xml"),
    # Игры (Иностранные)
    ("игры", "https://www.pcgamer.com/rss/"),
    ("игры", "https://www.eurogamer.net/feed"),
    # Нейросети (Русские)
    ("нейросети", "https://vc.ru/rss/tag/нейросети"),
    ("нейросети", "https://3dnews.ru/news/rss/"),
    ("нейросети", "https://www.ixbt.com/export/news.rss"),
    # Нейросети (Иностранные)
    ("нейросети", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("нейросети", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    # Reddit (Мульти-реддит: только горячее)
    ("reddit", "https://www.reddit.com/r/gaming+pcgaming+GTA6+artificial+OpenAI+StableDiffusion/hot/.rss"),
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

# === ФУНКЦИИ GITHUB ===
def get_github_trending():
    try:
        url = "https://api.github.com/search/repositories"
        params = {"q": "topic:artificial-intelligence created:>2026-09-01", "sort": "stars", "order": "desc", "per_page": 2}
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception:
        return []

def get_github_media(repo_full_name):
    try:
        url = f"https://api.github.com/repos/{repo_full_name}/readme"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200: return None
        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        images = re.findall(r'!\[[^\]]*\]\((https?://[^\s\)]+)\)', content)
        for img in images:
            if img.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return img
    except Exception:
        pass
    return None

# === ПАРСИНГ ТГ ===
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
                items.append({
                    "id": link, "link": link, "title": text[:80] + "...",
                    "summary": text[:1500], "time": time.time(), "topic": "новости",
                    "media": images[i] if i < len(images) else None
                })
    except Exception:
        pass
    return items

# === СБОР ДАННЫХ ===
def collect():
    items = []
    for topic, url in FEEDS:
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0")
            for e in feed.entries[:7]:
                link = e.get("link")
                title = clean(e.get("title"))
                if link and title:
                    items.append({
                        "id": e.get("id") or link, "link": link, "title": title,
                        "summary": clean(e.get("summary"))[:1500], "time": entry_time(e) or time.time(), "topic": topic,
                    })
        except Exception:
            pass
            
    for repo in get_github_trending():
        items.append({
            "id": f"github_{repo['id']}", "link": repo["html_url"], "title": repo["name"],
            "summary": repo.get("description") or "Описание отсутствует", "time": time.time(), "topic": "github",
            "media": get_github_media(repo["full_name"])
        })
        
    for ch in TG_CHANNELS:
        items.extend(fetch_from_telegram(ch))
        
    return items

def fetch_article(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        page = r.text
    except Exception:
        return "", None
    img = None
    m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I) or
         re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', page, re.I))
    if m: img = html.unescape(m.group(1))
    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I)
    text = " ".join(p for p in (clean(x) for x in paras) if len(p) > 60)
    return text[:4000], img

# === ГЕНЕРАЦИЯ ТЕКСТА (СТРОГИЙ ПРОМПТ) ===
def rewrite(item, article_text):
    if not GROQ_KEY: return None
    source_text = article_text or item["summary"]
    
    prompt = (
        f"Ты — топовый SMM-редактор Telegram-канала про игры и нейросети. Твоя задача — делать сочные, короткие посты.\n"
        f"Заголовок новости: {item['title']}\n"
        f"Исходный текст: {source_text}\n\n"
        
        "ПРАВИЛА (ОБЯЗАТЕЛЬНЫ К ИСПОЛНЕНИЮ):\n"
        "1. НИКАКИХ ЦИТАТ ИЗ ИСХОДНОГО ТЕКСТА. Перескажи суть своими словами.\n"
        "2. Пиши простым, живым языком (как для друзей). НИКАКИХ сложных философских терминов (онтология, семантика и т.д.).\n"
        "3. Текст (body) должен быть строго 2-3 предложения. Выдели главное.\n"
        "4. Шутка (joke) должна быть короткой (до 100 символов), понятной и смешной. Если новость серьезная (трагедия, скандал) — оставь поле joke пустым.\n"
        "5. Если исходный текст — это просто вопрос, мем или скучное обсуждение без фактов, ВЕРНИ ПУСТЫЕ СТРОКИ.\n\n"
        
        "Верни ответ СТРОГО в формате JSON (без markdown-разметки и без ```json):\n"
        "{\n"
        '  "emoji": "1 эмодзи",\n'
        '  "title": "Кликбейтный заголовок до 80 символов",\n'
        '  "body": "Текст поста (2-3 предложения)",\n'
        '  "joke": "Шутка или пустая строка",\n'
        '  "hashtags": ["тег1", "тег2"],\n'
        '  "image_prompt": "Промпт на английском для генерации картинки"\n'
        "}"
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    
    # ИСПОЛЬЗУЕМ САМУЮ УМНУЮ МОДЕЛЬ GROQ
    payload = {
        "model": "llama-3.3-70b-versatile", 
        "messages": [{"role": "user", "content": prompt}], 
        "response_format": {"type": "json_object"}, 
        "temperature": 0.6 # Снизили температуру, чтобы не выдумывал факты
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        # Очистка от случайных markdown-тегов
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        data = json.loads(raw)
        
        if data.get("title") and data.get("body"): 
            return data
        return None
    except Exception as ex:
        print("groq failed:", ex)
        return None

# === СБОРКА ПОСТА ===
def build_post(item, ai):
    esc = html.escape
    default_icon = "🎮" if item["topic"] == "игры" else "🤖" if item["topic"] == "нейросети" else "💻" if item["topic"] == "github" else "📰"
    category = "ИГРОВЫЕ НОВОСТИ" if item["topic"] == "игры" else "НЕЙРОСЕТИ" if item["topic"] == "нейросети" else "ТРЕНДЫ GITHUB" if item["topic"] == "github" else "НОВОСТИ"
    link = f'<a href="{esc(item["link"], quote=True)}">Читать источник</a>'
    
    if not ai: return None 

    icon = ai.get("emoji") or default_icon
    lines = [f"{icon} <b>{category}</b>", "", f"<b>{esc(str(ai['title']))}</b>", "", esc(str(ai.get("body", "")))]
    
    if ai.get("joke") and len(str(ai.get("joke"))) > 3: 
        lines += ["", f"😂 <i>{esc(str(ai['joke']))}</i>"]

    tags = []
    if BASE_TAGS.get(item["topic"]): tags.append(BASE_TAGS[item["topic"]])
    for t in (ai.get("hashtags") or [])[:3]:
        clean_t = re.sub(r"[^a-zA-Zа-яА-Я0-9]", "", str(t)).lower()
        if len(clean_t) >= 3:
            if f"#{clean_t}" not in tags: tags.append(f"#{clean_t}")
    if tags: lines += ["", " ".join(tags[:4])]

    # Автограф канала
    lines += ["", link, "🤖 <a href='https://t.me/PloxiQNews'>@PloxiQNews</a>"]
    
    return "\n".join(lines)

# === ОТПРАВКА ===
def send_post(item, ai, img):
    text = build_post(item, ai)
    if not text: return False

    if item.get("media"): img = item["media"]
    
    if not img or not img.startswith("http"):
        prompt = ai.get("image_prompt", "gaming setup neon")
        img = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?width=1080&height=720&nologo=true&seed={random.randint(1, 99999)}"

    if len(text) > 1000: text = text[:990] + "..."

    try:
        img_data = requests.get(img, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).content
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", data={"chat_id": CHAT_ID, "caption": text, "parse_mode": "HTML"}, files={"photo": img_data}, timeout=30)
        if r.ok: return True
    except Exception:
        pass

    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=30)
    return r.ok

# === ГЛАВНЫЙ ЦИКЛ ===
def main():
    seen = load_state()
    items = collect()
    
    fresh = [i for i in items if i["id"] not in seen and time.time() - i["time"] < MAX_AGE_HOURS * 3600]
    random.shuffle(fresh)
    
    candidates = fresh[:15] 
    posted_count = 0

    for item in candidates:
        if posted_count >= MAX_PER_RUN: 
            break

        article_text = item["summary"] if item["topic"] in ["github", "новости", "reddit"] else fetch_article(item["link"])[0]
        ai = rewrite(item, article_text)
        
        if not ai:
            continue # Если ИИ сказал "мусор" или не смог обработать

        is_test = os.environ.get("TEST_RUN", "false").lower() == "true"
        
        if is_test:
            print("🧪 Тестовый режим...")
        else:
            if posted_count == 0:
                delay = random.randint(0, 600) # Первый пост: 0-10 минут
            else:
                delay = random.randint(600, 2100) # Второй/третий: 10-35 минут
            
            if delay > 0:
                print(f"Ждем {delay // 60} мин. {delay % 60} сек. перед публикацией...")
                time.sleep(delay)

        if send_post(item, ai, item.get("media")):
            seen.append(item["id"])
            posted_count += 1
            save_state(seen) 
        time.sleep(5)

    print(f"За этот час успешно опубликовано: {posted_count}")

if __name__ == "__main__":
    main()
