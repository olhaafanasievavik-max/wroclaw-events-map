"""Забирает новые посты из Telegram-каналов и дописывает их в data/messages.jsonl.

Работает от имени вашего аккаунта через Telethon (как ещё одно устройство).

Переменные окружения:
  TG_API_ID, TG_API_HASH  - с https://my.telegram.org -> API development tools
  TG_SESSION              - строка сессии (StringSession). Получить: python src/fetch_telegram.py --login
  TG_CHANNELS             - через запятую: ссылки-инвайты, @username или id каналов.
                            Например: "https://t.me/+SsE-TDm4Lu5hMTE8,@my_inbox_channel"

Первый запуск локально:  python src/fetch_telegram.py --login
  -> спросит номер и код из Telegram, напечатает строку сессии. Её кладём в секрет
     GitHub Actions TG_SESSION (и в локальный .env для отладки).

Обычный запуск:          python src/fetch_telegram.py
  -> дочитывает всё новее последнего сохранённого id по каждому каналу.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JSONL = DATA / "messages.jsonl"
STATE = DATA / "fetch_state.json"


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


async def login():
    client = TelegramClient(StringSession(), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.start()
    print("\nСтрока сессии (сохраните в секрет TG_SESSION):\n")
    print(client.session.save())
    await client.disconnect()


async def resolve(client, ref: str):
    ref = ref.strip()
    if "t.me/+" in ref or ref.startswith("+"):
        # инвайт-ссылка закрытого канала: вы уже участник, поэтому ищем среди диалогов по хэшу
        from telethon.tl.functions.messages import CheckChatInviteRequest
        h = ref.split("+")[-1]
        inv = await client(CheckChatInviteRequest(h))
        if hasattr(inv, "chat"):
            return inv.chat
        raise RuntimeError(f"Вы не участник канала по ссылке {ref}: сначала вступите в него в Telegram")
    if ref.lstrip("-").isdigit():
        return await client.get_entity(int(ref))
    return await client.get_entity(ref)


async def fetch():
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    client = TelegramClient(StringSession(os.environ["TG_SESSION"]), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Сессия недействительна, выполните --login заново")
    DATA.mkdir(exist_ok=True)
    total = 0
    with JSONL.open("a", encoding="utf-8") as out:
        for ref in os.environ.get("TG_CHANNELS", "").split(","):
            if not ref.strip():
                continue
            chat = await resolve(client, ref)
            key = str(chat.id)
            last = state.get(key, 0)
            title = getattr(chat, "title", ref)
            new = []
            async for msg in client.iter_messages(chat, min_id=last, limit=200 if last else 30):
                if not msg.raw_text:   # raw_text: без markdown-разметки (**жирный** и т.п.)
                    continue
                src = title
                if getattr(msg, "forward", None) and msg.forward and getattr(msg.forward, "chat", None):
                    src = getattr(msg.forward.chat, "title", src)  # пересланное: помним исходный канал
                new.append({"id": f"{key}_{msg.id}", "date": msg.date.isoformat(), "source": src, "text": msg.raw_text})
                state[key] = max(state.get(key, 0), msg.id)
            for m in reversed(new):
                out.write(json.dumps(m, ensure_ascii=False) + "\n")
            total += len(new)
            print(f"{title}: новых сообщений {len(new)}")
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    await client.disconnect()
    print(f"всего добавлено: {total}")


if __name__ == "__main__":
    load_env()
    asyncio.run(login() if "--login" in sys.argv else fetch())
