import calendar
import html
import json
import os
import re
import time

import feedparser
import requests

TG_TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHANNEL"]  # например @mychannel
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

MAX_PER_RUN = 3      # сколько постов максимум за один запуск
MAX_AGE_HOURS = 24   # старше этого не постим
STATE_FILE = "posted.json"

# Хэштег, который ставится всегда, плюс 2–3 от нейросети.
BASE_TAGS = {"игры": "#игры", "нейросети": "#нейросети"}


def channel_footer():
    """Строка со ссылкой на твой канал в конце поста.
    Берётся из TG_CHANNEL (@имя). Если у канала нет @имени, задай секрет CHANNEL_LINK."""
    link = os.environ.get("CHANNEL_LINK", "").strip()
    if not link and CHAT_ID.startswith("@"):
        link = "https://t.me/" + CHAT_ID[1:]
    if not link:
        return ""
    return f'📢 <a href="{html.escape(link, quote=True)}">Подписывайся на канал</a>'

# Стиль постов. Меняй текст здесь, если хочешь другой тон.
STYLE = (
    "Живой, чуть неформальный тон для молодой аудитории, без канцелярита и кликбейта. "
    "Пиши коротко и по делу."
)

# Мёртвая ссылка не ломает бота: такие ленты просто пропускаются.
FEEDS = [
    # игры
    ("игры", "https://stopgame.ru/rss/rss_news.xml"),
    ("игры", "https://www.playground.ru/rss/news.xml"),
    ("игры", "https://www.pcgamer.com/rss/"),
    ("игры", "https://www.gamespot.com/feeds/news/"),
    ("игры", "https://www.ign.com/rss/v2/articles/feed"),
    # нейросети
    ("нейросети", "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru"),
    ("нейросети", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("нейросети", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("нейросети", "https://venturebeat.com/category/ai/feed/"),
]


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
    """Достаём текст статьи и картинку (og:image) прямо со страницы."""
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
    """Gemini превращает новость в аккуратный пост (JSON)."""
    if not GEMINI_KEY:
        return None
    source_text = article_text or item["summary"]
    prompt = (
        "Ты редактор Telegram-канала про игры и нейросети. "
        f"Стиль: {STYLE}\n"
        "Сделай из новости пост на русском. Верни JSON с полями:\n"
        '"emoji": один подходящий эмодзи по теме,\n'
        '"title": цепляющий заголовок до 80 символов,\n'
        '"lead": главная суть в 1–2 предложениях, до 250 символов,\n'
        '"points": список из 2–3 коротких ключевых фактов, каждый до 90 символов,\n'
        '"hashtags": 2–3 хэштега без символа #.\n'
        "Ничего не выдумывай, используй только факты из текста. Без markdown.\n\n"
        f"Заголовок: {item['title']}\nТекст: {source_text}"
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    )
    try:
        r = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=60,
        )
        r.raise_for_status()
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
        data = json.loads(raw)
        if not data.get("title") or not data.get("lead"):
            return None
        return data
    except Exception as ex:
        print("gemini failed:", ex)
        return None


def build_post(item, ai, with_points=True):
    esc = html.escape
    default_icon = "🎮" if item["topic"] == "игры" else "🤖"
    link = f'<a href="{esc(item["link"], quote=True)}">Читать источник</a>'

    if not ai:
        body = item["summary"][:300]
        lines = [f"{default_icon} <b>{esc(item['title'])}</b>"]
        if body:
            lines += ["", esc(body)]
        lines += ["", BASE_TAGS.get(item["topic"], ""), link]
        footer = channel_footer()
        if footer:
            lines += [footer]
        return "\n".join(lines)

    icon = ai.get("emoji") or default_icon
    lines = [f"{icon} <b>{esc(str(ai['title']))}</b>", "", esc(str(ai["lead"]))]

    points = [str(p) for p in (ai.get("points") or [])][:3]
    if with_points and points:
        lines += [""] + [f"▪️ {esc(p)}" for p in points]

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
    if img:
        if len(text) > 1000:
            text = build_post(item, ai, with_points=False)
        if len(text) <= 1000:
            ok = tg("sendPhoto", {
                "chat_id": CHAT_ID,
                "photo": img,
                "caption": text,
                "parse_mode": "HTML",
            })
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
    batch = fresh[:MAX_PER_RUN]

    for item in reversed(batch):  # старые первыми
        article_text, img = fetch_article(item["link"])
        ai = rewrite(item, article_text)
        if send_post(item, ai, img):
            seen.append(item["id"])
        time.sleep(5)

    save_state(seen)
    print(f"posted {len(batch)} of {len(fresh)} fresh items")


if __name__ == "__main__":
    main()
