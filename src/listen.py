"""Постоянно работающий цикл обновления карты.

Зачем: GitHub запускает workflow по расписанию не каждые 30 минут, как написано
в cron, а когда у него есть свободные мощности - на практике раз в 3-5 часов.
Поэтому вместо короткого запуска раз в полчаса держим один долгий: он сам
опрашивает Telegram в цикле, отвечает боту сразу и обновляет карту.

Что делает по кругу:
  * длинный опрос бота (соединение висит POLL_SECONDS секунд и рвётся,
    как только пришло сообщение) - ответ пользователю уходит мгновенно;
  * раз в CHANNEL_EVERY_MIN минут читает каналы через Telethon;
  * если что-то новое - пересобирает docs/events.json, коммитит и пушит;
  * живёт LISTEN_MINUTES минут и выходит, дальше его перезапускает расписание.

Переменные окружения (все необязательные, значения по умолчанию в коде):
  LISTEN_MINUTES, CHANNEL_EVERY_MIN, POLL_SECONDS, SKIP_BOT, SKIP_CHANNEL
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch_bot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

LISTEN_MINUTES = float(os.environ.get("LISTEN_MINUTES", "330"))
CHANNEL_EVERY_MIN = float(os.environ.get("CHANNEL_EVERY_MIN", "30"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "25"))
MAP_BASE = os.environ.get("MAP_URL", "https://olhaafanasievavik-max.github.io/wroclaw-events-map/")


def log(msg: str):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")


def run_script(name: str, *args: str) -> bool:
    p = sh(PY, str(ROOT / "src" / name), *args)
    for line in (p.stdout or "").splitlines()[-25:]:
        log(f"  {name}: {line}")
    if p.returncode != 0:
        log(f"  {name}: ОШИБКА\n{(p.stderr or '')[-1500:]}")
    return p.returncode == 0


def dirty() -> bool:
    return bool(sh("git", "status", "--porcelain", "--", "data", "docs").stdout.strip())


def commit_push() -> bool:
    sh("git", "add", "data", "docs")
    if sh("git", "diff", "--cached", "--quiet").returncode == 0:
        return False
    sh("git", "commit", "-m", f"update events {datetime.now(timezone.utc):%Y-%m-%d %H:%M}")
    for attempt in range(3):
        if sh("git", "push").returncode == 0:
            log("  запушено")
            return True
        log(f"  push не прошёл, попытка {attempt + 1}: подтягиваю чужие коммиты")
        sh("git", "pull", "--rebase")
    log("  push не удался")
    return False


def refresh_menu_button():
    """Кнопка «Карта» в боте ведёт на адрес с номером версии, чтобы Telegram не показывал старую страницу."""
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        return
    sha = (sh("git", "rev-parse", "--short", "HEAD").stdout or "").strip()
    if not sha:
        return
    try:
        import json
        import urllib.request
        body = {"menu_button": {"type": "web_app", "text": "Карта",
                                "web_app": {"url": f"{MAP_BASE}?v={sha}"}}}
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/setChatMenuButton",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as ex:
        log(f"  кнопка бота: {ex}")


def ensure_git_identity():
    if not (sh("git", "config", "user.email").stdout or "").strip():
        sh("git", "config", "user.email", "map-bot@users.noreply.github.com")
        sh("git", "config", "user.name", "map-bot")


def main():
    fetch_bot.load_env()
    ensure_git_identity()
    skip_bot = os.environ.get("SKIP_BOT") or not os.environ.get("TG_BOT_TOKEN")
    skip_channel = bool(os.environ.get("SKIP_CHANNEL"))
    log(f"старт: живу {LISTEN_MINUTES:.0f} мин, каналы раз в {CHANNEL_EVERY_MIN:.0f} мин, "
        f"бот {'выключен' if skip_bot else f'опрос по {POLL_SECONDS} с'}")

    started = time.monotonic()
    next_channel = 0.0
    cycles = 0
    while True:
        now = time.monotonic()
        need_build = False

        if not skip_channel and now >= next_channel:
            log("читаю каналы")
            if run_script("fetch_telegram.py"):
                need_build = True          # build сам поймёт, есть ли изменения
            next_channel = now + CHANNEL_EVERY_MIN * 60

        if not skip_bot:
            try:
                if fetch_bot.poll_once(POLL_SECONDS) > 0:
                    need_build = True
            except Exception as ex:
                log(f"бот: ошибка опроса {ex}")
                time.sleep(10)
        else:
            time.sleep(POLL_SECONDS)

        if need_build:
            run_script("build.py", "--translate")
        if dirty():
            if commit_push():
                refresh_menu_button()

        cycles += 1
        left = LISTEN_MINUTES * 60 - (time.monotonic() - started)
        if left <= 0:
            log(f"время вышло, выхожу (циклов {cycles}). Следующий запуск поднимет расписание.")
            return
        if cycles % 20 == 0:
            log(f"работаю, осталось {left / 60:.0f} мин")


if __name__ == "__main__":
    main()
