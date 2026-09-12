"""Забирает сообщения, присланные боту (пересланные посты и просто текст),
и дописывает их в data/messages.jsonl. Сервер не нужен: опрашиваем Bot API
методом getUpdates из GitHub Actions раз в полчаса (Telegram хранит
непрочитанные обновления 24 часа).

Переменные окружения:
  TG_BOT_TOKEN   - токен от @BotFather
  TG_OWNER_ID    - (необязательно) ваш числовой id в Telegram; если не задан,
                   владельцем становится первый, кто напишет боту, и это
                   запоминается в data/fetch_state.json. Чужие сообщения игнорируются.

После разбора бот отвечает в чат: сколько событий распознано и на какие даты.
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


def api(token: str, method: str, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=60) as r:
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


def main():
    load_env()
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        print("бот: нет TG_BOT_TOKEN, пропускаю")
        return
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    owner = int(os.environ.get("TG_OWNER_ID") or state.get("bot_owner") or 0)
    offset = state.get("bot_offset", 0)

    updates = api(token, "getUpdates", offset=offset, timeout=0, allowed_updates='["message"]')
    new_msgs, replies = [], []
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        chat_id = msg.get("chat", {}).get("id")
        sender = msg.get("from", {}).get("id")
        text = msg.get("text") or msg.get("caption") or ""
        if not chat_id or not text:
            continue
        if not owner:
            owner = sender
            state["bot_owner"] = owner
            print(f"бот: владелец назначен, id {owner}")
        if sender != owner:
            print(f"бот: игнорирую сообщение от {sender}")
            continue
        if text.startswith("/start"):
            replies.append((chat_id, "Привет! Пересылайте мне посты с событиями, и я добавлю их на карту "
                                     "при ближайшем обновлении (раз в 30 минут).\n\nКарта: " + MAP_URL))
            continue
        origin = msg.get("forward_origin") or {}
        source = (origin.get("chat") or {}).get("title") or origin.get("sender_user_name") or "переслано боту"
        ts = origin.get("date") or msg["date"]
        date = datetime.fromtimestamp(ts, tz=timezone.utc)
        url = None
        och = origin.get("chat") or {}
        if origin.get("type") == "channel" and origin.get("message_id"):
            url = (f"https://t.me/{och['username']}/{origin['message_id']}" if och.get("username")
                   else f"https://t.me/c/{str(och.get('id', '')).replace('-100', '', 1)}/{origin['message_id']}")
        rec = {"id": f"bot_{u['update_id']}", "date": date.isoformat(), "source": source, "text": text, "url": url}
        new_msgs.append(rec)

        evs = parse_message(text, date, source=source, message_id=rec["id"], url=url)
        if evs:
            days = sorted({e.date for e in evs})
            lines = [f"✅ Распознано событий: {len(evs)} ({', '.join(fmt_date(d) for d in days)})"]
            lines += [f"• {fmt_date(e.date)} {e.time_start or ''} {e.title[:50]}" for e in evs[:8]]
            if len(evs) > 8:
                lines.append(f"… и ещё {len(evs) - 8}")
            lines.append("Карта обновится в течение получаса.")
        else:
            lines = ["⚠️ Не нашёл в этом сообщении событий с датой и адресом (📍). "
                     "Если это событие, добавьте строки вида:\n📅 15 сентября\n⏰ 18:00\n📍 ул. Такая-то 1"]
        replies.append((chat_id, "\n".join(lines)))

    if new_msgs:
        DATA.mkdir(exist_ok=True)
        with JSONL.open("a", encoding="utf-8") as out:
            for m in new_msgs:
                out.write(json.dumps(m, ensure_ascii=False) + "\n")
    for chat_id, text in replies:
        try:
            api(token, "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")
        except Exception as ex:
            print(f"бот: не удалось ответить: {ex}")

    state["bot_offset"] = offset
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(f"бот: обновлений {len(updates)}, добавлено сообщений {len(new_msgs)}")


if __name__ == "__main__":
    main()
