import calendar
import html
import json
import os
import re
import time
import random
import base64

import feedparser
import requests

# === НАСТРОЙКИ ===
TG_TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHANNEL"]
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

MAX_PER_RUN = 1      
MAX_AGE_HOURS = 24   
STATE_FILE = "posted.json"

BASE_TAGS = {"игры": "#игры", "нейросети": "#нейросети", "github": "#github #opensource"}

# Стиль постов (адаптивный, без шаблонов)
STYLE = (
    "Ты профессиональный редактор. Проанализируй новость и сам выбери тон. "
    "Если новость смешная, странная или курьезная — пиши с юмором и иронией. "
    "Если новость серьезная (увольнения, суды, скандалы, трагедии) — пиши сдержанно и без шуток. "
    "Если новость про технологии — пиши с легким восторгом. "
    "Пиши живо, без канцелярита и воды."
)

FEEDS = [
    ("игры", "https://stopgame.ru/rss/rss_news.xml"),
    ("игры", "https://www.playground.ru/rss/news.xml"),
    ("игры", "https://www.pcgamer.com/rss/"),
    ("нейросети", "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru"),
    ("нейросети", "https://vc.ru/rss/tag/нейросети"),
    ("нейросети", "https://3dnews.ru/news/rss/"),
    ("нейросети", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("нейросети", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
]

def channel_footer():
    return ""

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

# === ФУНКЦИИ ДЛЯ GITHUB ===
def get_github_trending():
    """Ищет трендовые ИИ-репозитории на GitHub за последний месяц"""
    try:
        # Ищем репозитории по теме AI, созданные недавно, сортируем по звездам
        url = "https://api.github.com/search/repositories"
        params = {
            "q": "topic:artificial-intelligence created:>2026-10-01",
            "sort": "stars",
            "order": "desc",
            "per_page": 3
        }
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as ex:
        print("github api failed:", ex)
        return []

def get_github_media(repo_full_name):
    """Ищет картинки или гифки в README репозитория"""
    try:
        url = f"https://api.github.com/repos/{repo_full_name}/readme"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200:
            return None
        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        
        # Ищем ссылки на картинки/гифки
        images = re.findall(r'!\[[^\]]*\]\((https?://[^\s\)]+)\)', content)
        for img in images:
            if img.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return img
    except Exception as ex:
        print("github readme failed:", ex)
    return None

# === ОСНОВНЫЕ ФУНКЦИИ ===
def collect():
    items = []
    
    # 1. Собираем новости из RSS
    for topic, url in FEEDS:
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0")
            for e in feed.entries[:10]: # Берем по 10 новостей с каждого сайта
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
            
    # 2. Собираем тренды с GitHub
    repos = get_github_trending()
    for repo in repos:
        items.append({
            "id": f"github_{repo['id']}",
            "link": repo["html_url"],
            "title": repo["name"],
            "summary": repo.get("description") or "Описание отсутствует",
            "time": time.time(), # Считаем свежими
            "topic": "github",
            "media": get_github_media(repo["full_name"]) # Сохраняем найденную картинку
        })
        
    return items

def fetch_article(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        page = r.text
    except Exception as ex:
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
    
    # Если это GitHub, даем особую инструкцию
    is_github = item["topic"] == "github"
    github_rule = ""
    if is_github:
        github_rule = (
            "Это репозиторий с GitHub. Расскажи, какую крутую проблему он решает или что умеет. "
            "ЗАПРЕЩЕНО использовать шаблонные фразы (например: 'На GitHub залетел...', 'Вайбкодеры забираем', 'Сохраняем и покоряем'). "
            "Придумай уникальный, цепляющий заголовок."
        )

    prompt = (
        "Ты креативный SMM-редактор топового Telegram-канала про игры и нейросети. "
        f"Стиль: {STYLE}\n"
        f"{github_rule}\n"
        "Твоя главная цель — зацепить читателя. НЕ ИСПОЛЬЗУЙ ШАБЛОНЫ! Для каждой новости выбирай свой уникальный подход. "
        "Можешь начать с вопроса, с шокирующего факта или с короткого абзаца. "
        "Верни строго JSON с полями:\n"
        '"emoji": один подходящий эмодзи по теме,\n'
        '"title": цепляющий, кликбейтный (но правдивый) заголовок до 80 символов,\n'
        '"body": основной текст поста (2-4 предложения или список фактов). Пиши живо, с эмодзи внутри текста, если это уместно. До 500 символов,\n'
        '"joke": короткая шутка или ироничный вывод в конце. Если новость серьезная — оставь это поле пустым,\n'
        '"hashtags": 2-3 хэштега без символа #,\n'
        '"image_prompt": короткий промпт на английском для генерации картинки по теме новости.\n'
        "Ничего не выдумывай, используй только факты из текста. Если текст на английском - переведи всё на русский. Без markdown.\n\n"
        f"Заголовок: {item['title']}\nТекст: {source_text}"
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-oss-20b", 
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.8 
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
        data = json.loads(raw)
        if not data.get("title") or not data.get("body"):
            return None
        return data
    except Exception as ex:
        print("groq failed:", ex)
        return None

def build_post(item, ai):
    esc = html.escape
    default_icon = "🎮" if item["topic"] == "игры" else "🤖" if item["topic"] == "нейросети" else "💻"
    category = "ИГРОВЫЕ НОВОСТИ" if item["topic"] == "игры" else "НЕЙРОСЕТИ" if item["topic"] == "нейросети" else "ТРЕНДЫ GITHUB"
    
    link = f'<a href="{esc(item["link"], quote=True)}">Читать источник</a>'

    if not ai:
        return None 

    icon = ai.get("emoji") or default_icon
    lines = [f"{icon} <b>{category}</b>", ""]
    lines.append(f"<b>{esc(str(ai['title']))}</b>")
    lines.append("")
    lines.append(esc(str(ai.get("body", ""))))

    if ai.get("joke"):
        lines += ["", f"😂 <i>{esc(str(ai['joke']))}</i>"]

    tags = []
    base = BASE_TAGS.get(item["topic"])
    if base:
        tags.append(base)
    for t in (ai.get("hashtags") or [])[:3]:
        clean_t = re.sub(r"[^a-zA-Zа-яА-Я0-9]", "", str(t)).lower()
        if len(clean_t) >= 3:
            t = "#" + clean_t
            if t not in tags:
                tags.append(t)
    if tags:
        lines += ["", " ".join(tags[:4])]

    lines += ["", link]
    return "\n".join(lines)

def send_post(item, ai, img):
    text = build_post(item, ai)
    if not text:
        return False

    # Если есть картинка из GitHub, используем её. Иначе генерируем.
    if item["topic"] == "github" and item.get("media"):
        img = item["media"]
    
    if not img or not img.startswith("http"):
        prompt = ai.get("image_prompt", "gaming setup neon")
        prompt_encoded = requests.utils.quote(prompt)
        img = f"https://image.pollinations.ai/prompt/{prompt_encoded}?width=1080&height=720&nologo=true&seed={random.randint(1, 99999)}"
        print(f"Генерируем картинку: {img}")

    if len(text) > 1000:
        text = text[:990] + "..."

    try:
        img_data = requests.get(img, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).content
        
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "caption": text,
                "parse_mode": "HTML"
            },
            files={"photo": img_data},
            timeout=30
        )
        
        if r.ok:
            return True
        else:
            print(f"telegram photo error: {r.text}")
    except Exception as e:
        print("Image download/upload failed:", e)

    # Если картинка не ушла, отправляем текст
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=30
    )
    return r.ok

def main():
    seen = load_state()
    now = time.time()
    items = collect()

    fresh = [
        i for i in items
        if i["id"] not in seen and now - i["time"] < MAX_AGE_HOURS * 3600
    ]
    # Перемешиваем, чтобы посты были вперемешку (и игры, и ИИ, и гитхаб)
    random.shuffle(fresh)
    
    candidates = fresh[:10] 
    posted_count = 0

    for item in candidates:
        if posted_count >= MAX_PER_RUN:
            break

        # Для GitHub не нужно парсить статью, берем описание из API
        if item["topic"] == "github":
            article_text = item["summary"]
            img = None
        else:
            article_text, img = fetch_article(item["link"])
            
        ai = rewrite(item, article_text)
        
        if not ai:
            print(f"Пропускаем (ошибка ИИ): {item['title']}")
            continue

        is_test = os.environ.get("TEST_RUN", "false").lower() == "true"
        
        if is_test:
            print("🧪 Тестовый режим: публикуем сразу...")
        else:
            if random.random() < 0.8:
                delay = random.randint(600, 1200) 
            else:
                delay = random.randint(3600, 7200) 
                
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
