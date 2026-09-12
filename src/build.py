"""Сборка: сообщения -> события -> координаты -> docs/events.json для карты.

Источники сообщений (все необязательны):
  data/messages.jsonl   - то, что собрал fetch_telegram.py (по строке на сообщение)
  samples/messages.txt  - текстовый экспорт для отладки (флаг --samples)

Запуск:  python src/build.py [--samples] [--no-geo] [--translate]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parser import parse_message, split_export, dedupe, Event  # noqa: E402
from geocode import Geocoder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"


def load_messages(use_samples: bool) -> list[dict]:
    msgs = []
    jl = DATA / "messages.jsonl"
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                m = json.loads(line)
                m["date"] = datetime.fromisoformat(m["date"])
                msgs.append(m)
    if use_samples:
        txt = (ROOT / "samples" / "messages.txt").read_text(encoding="utf-8")
        msgs.extend(split_export(txt))
    return msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="добавить samples/messages.txt")
    ap.add_argument("--no-geo", action="store_true", help="не ходить в Nominatim")
    ap.add_argument("--translate", action="store_true", help="перевести названия через llm.py (нужен GEMINI_API_KEY)")
    args = ap.parse_args()

    msgs = load_messages(args.samples)
    print(f"сообщений: {len(msgs)}")

    events: list[Event] = []
    for m in msgs:
        evs = parse_message(m["text"], m["date"], source=m.get("source", ""), message_id=str(m.get("id", "")))
        print(f"  [{m['date']:%d.%m %H:%M}] {len(evs):2d} событий  {m['text'].strip().splitlines()[0][:60]}")
        events.extend(evs)
    events = dedupe(events)          # по тексту адреса
    print(f"уникальных событий: {len(events)}")

    if not args.no_geo:
        g = Geocoder(DATA)
        missing = []
        for e in events:
            r = g.geocode(e.address)
            if r:
                e.lat, e.lon, e.geo_query = r
            else:
                missing.append(e.address)
        if missing:
            print("не найдены адреса (добавьте в data/overrides.json):")
            for a in sorted(set(missing)):
                print("   -", a)
        before = len(events)
        events = dedupe(events)      # по координатам: разные адреса одного места
        if len(events) != before:
            print(f"склеено по координатам: {before - len(events)}, осталось {len(events)}")

    if args.translate:
        from llm import translate_titles
        translate_titles(events, DATA)

    events.sort(key=lambda e: (e.date, e.time_start or "99:99", e.title))
    DOCS.mkdir(exist_ok=True)
    out = {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "events": [e.to_dict() for e in events],
    }
    (DOCS / "events.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"записано docs/events.json")


if __name__ == "__main__":
    main()
