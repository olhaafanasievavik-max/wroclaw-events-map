"""Приём сообщений, присланных боту (пересланные посты и свой текст).

Используется двумя способами:
  * `python src/fetch_bot.py` - один опрос и выход (для отладки);
  * `poll_once()` из listen.py - длинный опрос в постоянно работающем слушателе.

Переменные окружения:
  TG_BOT_TOKEN   - токен от @BotFather
  TG_OWNER_ID    - (необязательно) ваш числовой id в Telegram; если не задан,
                   владельцем становится первый, кто напишет боту, и это
                   запоминается в data/fetch_state.json. Чужие сообщения игнорируются.

Бот отвечает сразу: сколько событий распознал и на какие даты.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parser import parse_message  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JSONL = DATA / "messages.jsonl"
STATE = DATA / "fetch_state.json"
MAP_URL = os.environ.get("MAP_URL", "https://olhaafanasievavik-max.github.io/wroclaw-events-map/")

HELP = ("Пересылайте мне посты с событиями - добавлю их на карту за пару минут.\n"
        "Можно и своими словами, главное чтобы были дата, время и адрес:\n"
        "📅 15 сентября\n⏰ 18:00\n📍 ul. Świdnicka 8\n\n"
        "Карта открывается кнопкой «Карта» внизу экрана.")


def api(token: str, method: str, _timeout: int = 60, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=_timeout) as r:
        res = json.loads(r.read().decode("utf-8"))
    if not res.get("ok"):
        raise RuntimeError(f"{method}: {res}")
    return res["result"]


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def fmt_date(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}"


def _load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def _save_state(state: dict):
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _known_ids() -> set[str]:
    if not JSONL.exists():
        return set()
    out = set()
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.add(json.loads(line)["id"])
            except Exception:
                pass
    return out


def _source_and_url(origin: dict) -> tuple[str, str | None]:
    """Кто автор поста и ссылка на оригинал (для пересланных из каналов)."""
    chat = origin.get("chat") or {}
    source = chat.get("title") or origin.get("sender_user_name") or "переслано боту"
    url = None
    if origin.get("type") == "channel" and origin.get("message_id"):
        if chat.get("username"):
            url = f"https://t.me/{chat['username']}/{origin['message_id']}"
        elif chat.get("id"):
            url = f"https://t.me/c/{str(chat['id']).replace('-100', '', 1)}/{origin['message_id']}"
    return source, url


def poll_once(long_poll: int = 0) -> int:
    """Забирает накопившиеся сообщения, отвечает на них и дописывает в messages.jsonl.

    long_poll - сколько секунд держать соединение в ожидании нового сообщения
    (0 = вернуться сразу). Возвращает число сохранённых сообщений.
    """
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        print("бот: нет TG_BOT_TOKEN, пропускаю", flush=True)
        return 0
    state = _load_state()
    owner = int(os.environ.get("TG_OWNER_ID") or state.get("bot_owner") or 0)
    offset = state.get("bot_offset", 0)

    updates = api(token, "getUpdates", _timeout=long_poll + 35, offset=offset,
                  timeout=long_poll, allowed_updates='["message"]')
    if not updates:
        return 0

    known, stored, group_with_text = _known_ids(), [], set()
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        sender = (msg.get("from") or {}).get("id")
        text = msg.get("text") or msg.get("caption") or ""
        group = msg.get("media_group_id")
        if not chat_id:
            continue
        if not owner:
            owner = sender
            state["bot_owner"] = owner
            print(f"бот: владелец назначен, id {owner}", flush=True)
        if sender != owner:
            print(f"бот: игнорирую сообщение от {sender}", flush=True)
            continue

        if not text:
            # Фото из альбома без подписи: текст пришёл в соседнем сообщении, молчим.
            print(f"бот: сообщение без текста (альбом={bool(group)})", flush=True)
            if not group:
                api(token, "sendMessage", chat_id=chat_id,
                    text="Здесь нет текста, а по картинке я событие не разберу. "
                         "Перешлите пост с текстом или напишите дату, время и адрес.")
            continue
        if text.startswith("/start") or text.startswith("/help"):
            api(token, "sendMessage", chat_id=chat_id, text=HELP, disable_web_page_preview="true")
            continue
        if group:
            group_with_text.add(group)

        origin = msg.get("forward_origin") or {}
        source, url = _source_and_url(origin)
        date = datetime.fromtimestamp(origin.get("date") or msg["date"], tz=timezone.utc)
        rec = {"id": f"bot_{u['update_id']}", "date": date.isoformat(),
               "source": source, "text": text, "url": url}
        if rec["id"] not in known:
            stored.append(rec)
            known.add(rec["id"])

        evs = parse_message(text, date, source=source, message_id=rec["id"], url=url)
        print(f"бот: сообщение от {source}, событий {len(evs)}", flush=True)
        if evs:
            days = sorted({e.date for e in evs})
            lines = [f"✅ Распознано событий: {len(evs)} ({', '.join(fmt_date(d) for d in days)})"]
            lines += [f"• {fmt_date(e.date)} {e.time_start or ''} {e.title[:50]}" for e in evs[:8]]
            if len(evs) > 8:
                lines.append(f"… и ещё {len(evs) - 8}")
            lines.append("Через пару минут появятся на карте.")
        else:
            lines = ["⚠️ Не нашёл здесь события с датой и адресом.\n\n" + HELP]
        try:
            api(token, "sendMessage", chat_id=chat_id, text="\n".join(lines), disable_web_page_preview="true")
        except Exception as ex:
            print(f"бот: не удалось ответить: {ex}", flush=True)

    if stored:
        DATA.mkdir(exist_ok=True)
        with JSONL.open("a", encoding="utf-8") as out:
            for m in stored:
                out.write(json.dumps(m, ensure_ascii=False) + "\n")
    state["bot_offset"] = offset
    _save_state(state)
    print(f"бот: обновлений {len(updates)}, сохранено сообщений {len(stored)}", flush=True)
    return len(stored)


if __name__ == "__main__":
    load_env()
    poll_once()
