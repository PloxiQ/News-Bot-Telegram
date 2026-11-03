import calendar
import html
import json
import os
import re
import time
import random

import feedparser
import requests

# === НАСТРОЙКИ ===
TG_TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHANNEL"]
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

MAX_PER_RUN = 1      
MAX_AGE_HOURS = 24   
STATE_FILE = "posted.json"

# Дефолтная обложка, если у новости нет картинки (стильный неоновый гейминг/ИИ)
DEFAULT_IMG = "https://images.unsplash.com/photo-1550745165-9bc0b252726f?q=80&w=1080&auto=format&fit=crop"

BASE_TAGS = {"игры": "#игры", "нейросети": "#нейросети"}

STYLE = (
    "Живой, динамичный тон успешного Telegram-блогера. Без канцелярита, воды и кликбейта. "
    "Пиши так, чтобы хотелось переслать другу."
)

FEEDS = [
    # Игры
    ("игры", "https://stopgame.ru/rss/rss_news.xml"),
    ("игры", "https://www.playground.ru/rss/news.xml"),
    ("игры", "https://www.pcgamer.com/rss/"),
    # Нейросети (русские)
    ("нейросети", "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru"),
    ("нейросети", "https://vc.ru/rss/tag/нейросети"),
    ("нейросети", "https://3dnews.ru/news/rss/"),
    # Нейросети (иностранные)
    ("нейросети", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("нейросети", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
]

def channel_footer():
    link = os.environ.get("CHANNEL_LINK", "").strip()
    if not link and CHAT_ID.startswith("@"):
        link = "https://t.me/" + CHAT_ID[1:]
    if not link:
        return ""
    return f'📢 <a href="{html.escape(link, quote=True)}">Подписывайся на канал</a>'

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_state(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-500:], f, ensure_ascii=False)

def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return calendar.timegm(t) if t else None

def collect():
    items = []
    for topic, url in FEEDS:
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0")
            for e in feed.entries[:15]:
                link = e.get("link")
                title = clean(e.get("title"))
                if not link or not title:
                    continue
                items.append({
                    "id": e.get("id") or link,
                    "link": link,
                    "title": title,
                    "summary": clean(e.get("summary"))[:1500],
                    "time": entry_time(e) or time.time(),
                    "topic": topic,
                })
        except Exception as ex:
            print("feed failed:", url, ex)
    return items

def fetch_article(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        page = r.text
    except Exception as ex:
        print("article fetch failed:", url, ex)
        return "", None

    img = None
    m = (
        re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I)
        or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', page, re.I)
    )
    if m:
        img = html.unescape(m.group(1))

    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I)
    text = " ".join(p for p in (clean(x) for x in paras) if len(p) > 60)
    return text[:5000], img

def rewrite(item, article_text):
    if not GROQ_KEY:
        return None

    source_text = article_text or item["summary"]
    
    # Улучшенный промпт для идеального формата
    prompt = (
        "Ты главный редактор топового Telegram-канала про игры и нейросети. "
        f"Стиль: {STYLE}\n"
        "Твоя задача — сделать из этой новости идеальный пост на русском языке. "
        "Верни строго JSON с полями:\n"
        '"emoji": один подходящий эмодзи по теме (например 🎮 или 🤖),\n'
        '"title": цепляющий, но правдивый заголовок до 80 символов,\n'
        '"context": подробный контекст (2-3 предложения). Объясни суть, предысторию или почему это важно. До 400 символов,\n'
        '"points": список из 2-3 коротких ключевых фактов (каждый до 90 символов),\n'
        '"joke": короткая шутка, ироничный комментарий или мем. Если новость серьезная, сделай шутку сдержанной и саркастичной. До 150 символов,\n'
        '"hashtags": 2-3 хэштега без символа #.\n'
        "Ничего не выдумывай, используй только факты из текста. Если текст на английском - переведи всё на русский. Без markdown.\n\n"
        f"Заголовок: {item['title']}\nТекст: {source_text}"
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-oss-20b", 
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.7 
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
        data = json.loads(raw)
        if not data.get("title") or not data.get("context"):
            return None
        return data
    except Exception as ex:
        print("groq failed:", ex)
        return None

def build_post(item, ai, with_points=True):
    esc = html.escape
    default_icon = "🎮" if item["topic"] == "игры" else "🤖"
    category = "ИГРОВЫЕ НОВОСТИ" if item["topic"] == "игры" else "НЕЙРОСЕТИ"
    
    link = f'<a href="{esc(item["link"], quote=True)}">Читать источник</a>'

    if not ai:
        return None # Если нейросеть не ответила, лучше пропустить, чем постить сырой текст

    icon = ai.get("emoji") or default_icon
    
    # Формируем красивый заголовок
    lines = [f"{icon} <b>{category}</b>", ""]
    lines.append(f"<b>{esc(str(ai['title']))}</b>")
    lines.append("")
    lines.append(esc(str(ai["context"])))

    points = [str(p) for p in (ai.get("points") or [])][:3]
    if with_points and points:
        lines += [""] + [f"🔹 {esc(p)}" for p in points]

    if ai.get("joke"):
        lines += ["", f"😂 <i>{esc(str(ai['joke']))}</i>"]

    tags = []
    base = BASE_TAGS.get(item["topic"])
    if base:
        tags.append(base)
    for t in (ai.get("hashtags") or [])[:3]:
        t = "#" + re.sub(r"\W", "", str(t)).lower()
        if len(t) > 1 and t not in tags:
            tags.append(t)
    if tags:
        lines += ["", " ".join(tags[:4])]

    lines += ["", link]
    footer = channel_footer()
    if footer:
        lines += [footer]
    return "\n".join(lines)

def tg(method, payload):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
        json=payload,
        timeout=30,
    )
    if not r.ok:
        print("telegram error:", method, r.status_code, r.text)
    return r.ok

def send_post(item, ai, img):
    text = build_post(item, ai)
    if not text:
        return False # Пропускаем, если не смогли сгенерировать текст

    # Если картинки нет, ставим дефолтную
    if not img:
        img = DEFAULT_IMG

    if len(text) > 1000:
        text = build_post(item, ai, with_points=False)
        
    if len(text) <= 1000:
        try:
            img_data = requests.get(img, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).content
            ok = tg("sendPhoto", {
                "chat_id": CHAT_ID,
                "photo": img_data,
                "caption": text,
                "parse_mode": "HTML",
            })
        except Exception as e:
            print("image download failed:", e)
            ok = False
        
        if ok:
            return True
            
    return tg("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    })

def main():
    seen = load_state()
    now = time.time()
    items = collect()

    fresh = [
        i for i in items
        if i["id"] not in seen and now - i["time"] < MAX_AGE_HOURS * 3600
    ]
    fresh.sort(key=lambda i: i["time"], reverse=True)
    
    # Берем с запасом, чтобы если одна новость сломается, взять следующую
    candidates = fresh[:5] 
    posted_count = 0

    for item in candidates:
        if posted_count >= MAX_PER_RUN:
            break

        article_text, img = fetch_article(item["link"])
        ai = rewrite(item, article_text)
        
        # Если нейросеть не смогла обработать - идем к следующей
        if not ai:
            print(f"Пропускаем (ошибка ИИ): {item['title']}")
            continue

        is_test = os.environ.get("TEST_RUN", "false").lower() == "true"
        
        if is_test:
            print("🧪 Тестовый режим: публикуем сразу без задержки...")
        else:
            delay = random.randint(600, 6600) 
            print(f"Ждем {delay // 60} мин. перед публикацией...")
            time.sleep(delay)

        if send_post(item, ai, img):
            seen.append(item["id"])
            posted_count += 1
            
        time.sleep(5)

    save_state(seen)
    print(f"Успешно опубликовано: {posted_count}")

if __name__ == "__main__":
    main()
    
