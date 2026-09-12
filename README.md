# Wrocław Events Map

Личная карта событий Вроцлава из постов Telegram-канала «Вроцлав Интересно».
Всё бесплатно: Python, GitHub Actions, GitHub Pages, OpenStreetMap, Gemini (бесплатный тариф).

## Как это работает

```
Telegram-канал(ы) ──► src/fetch_telegram.py ──► data/messages.jsonl
                                                       │
                                     src/build.py (parser → geocode → llm)
                                                       │
                                                docs/events.json ──► docs/index.html (карта)
```

- `src/parser.py` – разбирает три формата постов (утренний дайджест, тематическая подборка, анонс одного события), даты вида «12-13 сентября» превращает в две записи, посты без даты (вакансии, подборки мест) пропускает.
- `src/geocode.py` – Nominatim (OpenStreetMap), сначала точный уличный адрес, потом название площадки. Кэш в `data/geocache.json`, ручные правки в `data/overrides.json`.
- `src/llm.py` – перевод польских названий на русский через Gemini, кэш в `data/translations.json`. Без ключа просто пропускается.
- `docs/index.html` – карта Leaflet: выбор даты, метки-эмодзи, при наведении описание, по клику карточка с кнопками «Google Calendar», «.ics» и «Маршрут».

## Локальная проверка на примерах

```bash
python src/build.py --samples
python -m http.server 8765 --directory docs
```

и открыть http://localhost:8765.

## Запуск по-настоящему (один раз)

1. **Ключи Telegram.** На https://my.telegram.org → API development tools → создать приложение. Скопировать `api_id` и `api_hash` в `.env` (шаблон в `.env.example`).
2. **Сессия.** Локально: `pip install -r requirements.txt`, затем `python src/fetch_telegram.py --login`. Скрипт спросит номер и код из Telegram и напечатает строку сессии. Записать её в `.env` как `TG_SESSION`.
3. **Каналы.** В `TG_CHANNELS` через запятую: инвайт-ссылка канала «Вроцлав Интересно» и, если хочется, свой приватный канал-«входящие», куда вы будете пересылать посты с телефона.
4. **Gemini.** На https://aistudio.google.com получить бесплатный API-ключ, записать в `GEMINI_API_KEY`.
5. **GitHub.** Создать репозиторий, залить папку. В Settings → Secrets and variables → Actions добавить те же пять значений: `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`, `TG_CHANNELS`, `GEMINI_API_KEY`. В Settings → Pages выбрать Branch `main`, папку `/docs`.
6. Во вкладке Actions запустить workflow «update map» вручную. Дальше он сам ходит в Telegram каждые 30 минут и обновляет карту.

Карта будет по адресу `https://<ваш-логин>.github.io/<репозиторий>/`.

## Если адрес нашёлся не там

В `data/overrides.json` добавить строку с адресом ровно как в посте и правильными координатами (их можно взять из Google Maps: правый клик по точке). При следующей сборке override имеет приоритет над кэшем.

## Что дальше

- Telegram-бот с кнопкой, открывающей карту как Mini App.
- Напоминания о выбранных событиях.
