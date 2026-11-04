# PloxiQ News 🤖📰

[🇷🇺 Русский](#-русский) | [🇺🇦 Українська](#-українська) | [🇬🇧 English](#-english)

---

## 🇷🇺 Русский

**PloxiQ News** — это продвинутый Telegram-бот, который автоматически собирает, анализирует и публикует свежие новости из мира видеоигр, искусственного интеллекта и трендов с GitHub.

### ✨ Возможности:
- 📡 **Сбор новостей:** Работает с RSS-лентами популярных игровых порталов и AI-ресурсов.
- 💻 **Тренды GitHub:** Ищет свежие ИИ-репозитории, парсит их README и вытаскивает демо-картинки/гифки.
- 🧠 **Умный ИИ-редактор (Groq):** Нейросеть сама выбирает тон поста (юмор или сдержанность) и придумывает уникальную структуру без шаблонов.
- 🖼️ **Умные картинки:** Если у новости нет картинки, бот генерирует её через Pollinations AI.
- 🛡️ **Анти-дубликат:** Бот запоминает опубликованное и не спамит одинаковыми новостями.
- ⏱ **Режим "Живого человека":** Бот публикует посты с умной задержкой (80% быстрых публикаций, 20% долгих пауз).

### 🚀 Как запустить (через GitHub Actions):
1. Форкните этот репозиторий.
2. Создайте бота через [@BotFather](https://t.me/BotFather) и получите **Токен**.
3. Добавьте бота в ваш Telegram-канал как администратора (с правом публикации).
4. В настройках репозитория (Settings -> Secrets and variables -> Actions) добавьте секреты:
   - `TG_TOKEN` (токен бота)
   - `TG_CHANNEL` (юзернейм канала, например `@my_channel`)
   - `GROQ_API_KEY` (ключ от console.groq.com)
5. Перейдите во вкладку **Actions** и запустите воркфлоу `news-bot`.

### ⚠️ Важно:
Для корректной работы бота в настройках репозитория (Settings -> Actions -> General -> Workflow permissions) должно быть включено **Read and write permissions**.

---

## 🇺🇦 Українська

**PloxiQ News** — це просунутий Telegram-бот, який автоматично збирає, аналізує та публікує свіжі новини зі світу відеоігор, штучного інтелекту та трендів з GitHub.

### ✨ Можливості:
- 📡 **Збір новин:** Працює з RSS-стрічками популярних ігрових порталів та AI-ресурсів.
- 💻 **Тренди GitHub:** Шукає свіжі ІІ-репозиторії, парсить їх README та витягує демо-картинки/гіфки.
- 🧠 **Розумний ІІ-редактор (Groq):** Нейромережа сама обирає тон посту (гумор чи стриманість) та придумує унікальну структуру без шаблонів.
- 🖼️ **Розумні картинки:** Якщо у новини немає картинки, бот генерує її через Pollinations AI.
- 🛡️ **Анти-дублікат:** Бот запам'ятовує опубліковане і не спамить однаковими новинами.
- ⏱ **Режим "Живої людини":** Бот публікує пости з розумною затримкою (80% швидких публікацій, 20% довгих пауз).

### 🚀 Як запустити (через GitHub Actions):
1. Форкніть цей репозиторій.
2. Створіть бота через [@BotFather](https://t.me/BotFather) та отримайте **Токен**.
3. Додайте бота у ваш Telegram-канал як адміністратора (з правом публікації).
4. У налаштуваннях репозиторію (Settings -> Secrets and variables -> Actions) додайте секрети:
   - `TG_TOKEN` (токен бота)
   - `TG_CHANNEL` (юзернейм каналу, наприклад `@my_channel`)
   - `GROQ_API_KEY` (ключ від console.groq.com)
5. Перейдіть на вкладку **Actions** та запустіть воркфлоу `news-bot`.

### ⚠️ Важливо:
Для коректної роботи бота в налаштуваннях репозиторію (Settings -> Actions -> General -> Workflow permissions) має бути увімкнено **Read and write permissions**.

---

## 🇬🇧 English

**PloxiQ News** is an advanced Telegram bot that automatically collects, analyzes, and publishes fresh news from the world of video games, artificial intelligence, and GitHub trends.

### ✨ Features:
- 📡 **News Collection:** Works with RSS feeds from popular gaming portals and AI resources.
- 💻 **GitHub Trends:** Searches for fresh AI repositories, parses their READMEs, and extracts demo images/GIFs.
- 🧠 **Smart AI Editor (Groq):** The AI chooses the tone of the post (humor or serious) and generates a unique structure without templates.
- 🖼️ **Smart Images:** If a news item lacks an image, the bot generates one via Pollinations AI.
- 🛡️ **Anti-duplicate:** The bot remembers what has been published and won't spam identical news.
- ⏱ **"Live Human" Mode:** The bot publishes posts with a smart delay (80% fast publications, 20% long pauses).

### 🚀 How to run (via GitHub Actions):
1. Fork this repository.
2. Create a bot via [@BotFather](https://t.me/BotFather) and get your **Token**.
3. Add the bot to your Telegram channel as an administrator (with post permissions).
4. In repository settings (Settings -> Secrets and variables -> Actions), add secrets:
   - `TG_TOKEN` (bot token)
   - `TG_CHANNEL` (channel username, e.g., `@my_channel`)
   - `GROQ_API_KEY` (key from console.groq.com)
5. Go to the **Actions** tab and run the `news-bot` workflow.

### ⚠️ Important:
For the bot to work correctly, make sure **Read and write permissions** are enabled in your repository settings (Settings -> Actions -> General -> Workflow permissions).
