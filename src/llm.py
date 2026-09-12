"""Перевод названий событий на русский через Gemini (бесплатный тариф Google AI Studio).

Нужна переменная окружения GEMINI_API_KEY. Переводы кэшируются в data/translations.json,
так что каждое название уходит в модель один раз.

Переводим только названия, в которых есть латиница (польский/английский).
Адреса не трогаем. Имена собственные модель оставляет как есть и добавляет
русское пояснение: «Targi Jedwab» -> «Ярмарка Jedwab (винтажный маркет)».
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

# Порядок перебора: если модель недоступна (404) или перегружена (503), берём следующую
MODELS = [m for m in os.environ.get("GEMINI_MODEL", "gemini-3.6-flash,gemini-3.5-flash,gemini-flash-latest").split(",") if m]
LATIN_RE = re.compile(r"[A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż]{3,}")

PROMPT = """Ты переводчик афиши Вроцлава для русскоязычного читателя, который не знает польского.
Ниже список названий событий (польский, английский, иногда смешанные с русским).
Для каждого дай короткий русский вариант: переведи смысл, имена собственные
(названия площадок, фестивалей, брендов) оставь в оригинале, при необходимости
добавь в скобках пояснение, что это. Если название уже целиком по-русски, верни его без изменений.
Ответь строго JSON-объектом {"переводы": ["...", "..."]} в том же порядке, без пояснений.

Названия:
"""


def _call_gemini(titles: list[str]) -> list[str]:
    key = os.environ["GEMINI_API_KEY"]
    body = {
        "contents": [{"parts": [{"text": PROMPT + "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    data = None
    last_err: Exception | None = None
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as ex:
            last_err = ex
            if ex.code in (404, 429, 503):
                print(f"перевод: {model} недоступна ({ex.code}), пробую следующую")
                continue
            raise
    if data is None:
        raise RuntimeError(f"все модели Gemini недоступны: {last_err}")
    # у моделей с «размышлениями» текст лежит в части без thought=True
    parts = [p for p in data["candidates"][0]["content"]["parts"] if "text" in p and not p.get("thought")]
    text = parts[-1]["text"]
    parsed = json.loads(text)
    out = parsed.get("переводы") or parsed.get("translations") or next(iter(parsed.values()))
    if len(out) != len(titles):
        raise ValueError(f"gemini вернул {len(out)} переводов на {len(titles)} названий")
    return [str(x).strip() for x in out]


def translate_titles(events, data_dir: Path, batch: int = 15) -> None:
    cache_path = data_dir / "translations.json"
    cache: dict[str, str] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    need = sorted({e.title for e in events if LATIN_RE.search(e.title) and e.title not in cache})
    if need and not os.environ.get("GEMINI_API_KEY"):
        print(f"перевод: нет GEMINI_API_KEY, пропущено {len(need)} названий")
    elif need:
        done = 0
        for i in range(0, len(need), batch):
            chunk = need[i:i + batch]
            try:
                for t, ru in zip(chunk, _call_gemini(chunk)):
                    cache[t] = ru
                    done += 1
            except Exception as ex:
                print(f"перевод: ошибка на пачке {i // batch + 1}: {ex}; продолжаю")
                continue
        data_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"перевод: переведено {done} из {len(need)} названий")
    for e in events:
        if e.title in cache and cache[e.title] != e.title:
            e.title_ru = cache[e.title]
