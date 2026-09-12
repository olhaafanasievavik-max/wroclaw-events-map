"""Разбор сообщений канала в список событий.

Поддерживаемые форматы:
  1. Утренний дайджест: «Сегодня суббота, 12 сентября.» + блоки «<эмодзи> <время> - <название>».
  2. Тематический пост: дата в вводном абзаце («В пятницу, 11 сентября,» / «Уже завтра»), блоки как в дайджесте.
  3. Анонс одного события: строки 📅 / ⏰ / 📍 / 🎟, название из первой строки.

Сообщения без даты (вакансии, подборки мест) пропускаются.
Работает без внешних зависимостей. Для перевода названий и
нестандартных постов есть отдельный модуль llm.py, он опционален.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}
MONTH_RE = "|".join(MONTHS)

TIME = r"(\d{1,2})[:.](\d{2})"
TIME_RANGE_RE = re.compile(rf"(?:с\s+)?{TIME}(?:\s*[-–—]\s*{TIME})?")
HEADER_RE = re.compile(r"^(?P<emoji>[^\w\s«»\"'(\[]+)?\s*(?P<rest>.*)$")
LABEL = r"(?:(?:Где|Место|Місце|Адрес|Когда|Дата|Старт|Начало|Время|Час)\s*[:：]\s*)?"
ADDRESS_RE = re.compile(rf"^📍\s*{LABEL}(.+)$")
PRICE_RE = re.compile(r"^🎟\s*(.+)$")
DATE_LINE_RE = re.compile(rf"^📅\s*{LABEL}(.+)$")
TIME_LINE_RE = re.compile(rf"^[⏰🕐-🕧]\s*{LABEL}(.+)$")
LINK_RE = re.compile(r"^(?:🌐|🔗)")
MARKER_RE = re.compile(r"^[📍🎟📅⏰🕐-🕧🌐🔗]")
# продолжение адреса строкой ниже: «ul. Zwycięska 2, Wrocław»
STREET_LINE_RE = re.compile(r"^(?:ul\.|pl\.|al\.|ulica|aleja|plac|przejście|rynek|bulwar)\s|^[\w\s.]+\s\d+[\w/-]*(?:,\s*Wrocław)?$", re.I)
WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4, "суббота": 5, "воскресенье": 6,
    "понеділок": 0, "вівторок": 1, "середа": 2, "четвер": 3, "п'ятниця": 4, "субота": 5, "неділя": 6,
}
# Повторяющиеся события: «по субботам и воскресеньям», «по выходным», «ежедневно»
RECUR_HORIZON_DAYS = 56   # без даты окончания расписываем на 8 недель вперёд
RECUR_WORDS = [
    (r"ежедневно|каждый день|щодня|codziennie", set(range(7)), "ежедневно"),
    (r"по выходным|каждые выходные|у вихідні|щовихідних", {5, 6}, "по выходным"),
    (r"по будням|у будні", {0, 1, 2, 3, 4}, "по будням"),
]
RECUR_DAY_RE = re.compile(
    r"(?:по|кажд\w+|що)\s*(понедельник|вторник|сред|четверг|пятниц|суббот|воскресень|"
    r"понеділ|вівтор|серед|четвер|п['’]ятниц|субот|неділ)\w*", re.I)
RECUR_DAY_MAP = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6,
                 "понеділ": 0, "вівтор": 1, "серед": 2, "четвер": 3, "п'ятниц": 4, "п’ятниц": 4, "субот": 5, "неділ": 6}
RECUR_DAY_LABEL = ["по понедельникам", "по вторникам", "по средам", "по четвергам", "по пятницам", "по субботам", "по воскресеньям"]
UNTIL_RE = re.compile(rf"(?:до|по|until)\s+(\d{{1,2}})\s+({MONTH_RE})", re.I)
MD_BOLD_RE = re.compile(r"\*\*|__")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def clean_markdown(text: str) -> str:
    text = MD_LINK_RE.sub(r"\1", text)
    return MD_BOLD_RE.sub("", text)
EXPORT_HEADER_RE = re.compile(r"^\[(\d{2})\.(\d{2})\.(\d{4}) (\d{1,2}):(\d{2})\] ([^:]+):.*$", re.M)

# «Сегодня суббота, 12 сентября.» / «В пятницу, 11 сентября,» / «📅 12 сентября, суббота»
DAY_MONTH_RE = re.compile(rf"(\d{{1,2}})(?:\s*[-–—]\s*(\d{{1,2}}))?\s+({MONTH_RE})", re.I)
# «12.09» / «12-13.09» / «12.09.2026»
DAY_NUM_RE = re.compile(r"\b(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\.(\d{2})(?:\.(\d{4}))?\b")
TOMORROW_RE = re.compile(r"\bзавтра\b", re.I)
TODAY_RE = re.compile(r"\bсегодня\b", re.I)


@dataclass
class Event:
    date: str                     # ISO YYYY-MM-DD
    title: str
    address: str
    time_start: str | None = None  # HH:MM
    time_end: str | None = None
    description: str = ""
    price: str | None = None
    emoji: str = ""
    title_ru: str | None = None   # перевод, заполняет llm.py
    source: str = ""              # канал
    message_id: str = ""
    lat: float | None = None
    lon: float | None = None
    geo_query: str | None = None  # какой запрос сработал в геокодере
    day_index: int = 1            # какой это день события: «(2 из 3)»
    day_count: int = 1
    recurrence: str | None = None  # «по субботам и воскресеньям», если событие повторяется
    url: str | None = None        # ссылка на исходный пост

    def key(self) -> tuple:
        return (self.date, self.title.lower().strip(), self.address.lower().strip())

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- вспомогательное ----------

def _resolve_year(day: int, month: int, ref: date) -> date:
    """Год в сообщении не указан: берём год сообщения, а если дата вышла
    сильно раньше даты сообщения, значит речь про следующий год."""
    try:
        d = date(ref.year, month, day)
    except ValueError:
        return ref
    if (ref - d).days > 180:
        d = date(ref.year + 1, month, day)
    return d


def _parse_time(text: str) -> tuple[str | None, str | None]:
    m = TIME_RANGE_RE.search(text)
    if not m:
        return None, None
    h1, m1, h2, m2 = m.groups()
    start = f"{int(h1):02d}:{m1}"
    end = f"{int(h2):02d}:{m2}" if h2 else None
    return start, end


def _dates_from_text(text: str, ref: date) -> list[date]:
    """Все явные даты в тексте, с учётом диапазонов «12-13 сентября»."""
    out: list[date] = []
    for m in DAY_MONTH_RE.finditer(text):
        d1, d2, mon = m.groups()
        month = MONTHS[mon.lower()]
        first = _resolve_year(int(d1), month, ref)
        out.append(first)
        if d2:
            last = _resolve_year(int(d2), month, ref)
            cur = first
            while cur < last:
                cur += timedelta(days=1)
                out.append(cur)
    for m in DAY_NUM_RE.finditer(text):
        d1, d2, mon, year = m.groups()
        month = int(mon)
        if not 1 <= month <= 12:
            continue
        first = date(int(year), month, int(d1)) if year else _resolve_year(int(d1), month, ref)
        out.append(first)
        if d2:
            last = date(int(year), month, int(d2)) if year else _resolve_year(int(d2), month, ref)
            cur = first
            while cur < last:
                cur += timedelta(days=1)
                out.append(cur)
    # уникальные, в порядке появления
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def _message_dates(text: str, ref: date) -> list[date]:
    """Дата(ы), к которым относится сообщение целиком."""
    lines = [l.strip() for l in text.splitlines()]
    # 1. строка 📅
    for l in lines:
        m = DATE_LINE_RE.match(l)
        if m:
            ds = _dates_from_text(m.group(1), ref)
            if ds:
                return ds
    # 2. «Сегодня ..., 12 сентября» / «В пятницу, 11 сентября»
    intro = "\n".join(lines[:8])
    ds = _dates_from_text(intro, ref)
    if ds:
        return ds[:1]
    # 3. «завтра» / «сегодня» без числа
    if TOMORROW_RE.search(intro):
        return [ref + timedelta(days=1)]
    if TODAY_RE.search(intro):
        return [ref]
    return []


def _recurrence(text: str) -> tuple[set[int], str] | None:
    """«По субботам и воскресеньям» -> ({5, 6}, «по субботам и воскресеньям»)."""
    low = text.lower()
    for pat, days, label in RECUR_WORDS:
        if re.search(pat, low):
            return days, label
    if not RECUR_DAY_RE.search(low):
        return None
    # «по субботам и воскресеньям»: после «по» перечислены все дни, собираем их без префикса
    days = set()
    for m in re.finditer(r"\b(понедельник|вторник|сред(?=ам|у|ы)|четверг|пятниц|суббот|воскресень|"
                         r"понеділ|вівтор|серед(?=ам|у|и)|четвер|п['’]ятниц|субот|неділ)", low):
        stem = m.group(1).replace("’", "'")
        if stem in RECUR_DAY_MAP:
            days.add(RECUR_DAY_MAP[stem])
    if not days:
        return None
    labels = [RECUR_DAY_LABEL[d] for d in sorted(days)]
    label = labels[0] if len(labels) == 1 else ", ".join(l for l in labels[:-1]) + " и " + labels[-1].split(" ", 1)[1]
    return days, label


def _expand_recurrence(dates: list[date], weekdays: set[int], text: str, ref: date) -> list[date]:
    """Расписываем повторяющееся событие по дням: от старта до «до 30 сентября» или на 8 недель."""
    start = min(dates) if dates else ref
    m = UNTIL_RE.search(text)
    end = _resolve_year(int(m.group(1)), MONTHS[m.group(2).lower()], ref) if m else start + timedelta(days=RECUR_HORIZON_DAYS)
    if len(dates) > 1:                # «5-30 сентября, по выходным»
        return [d for d in dates if d.weekday() in weekdays]
    out, cur = [], start
    while cur <= end:
        if cur.weekday() in weekdays:
            out.append(cur)
        cur += timedelta(days=1)
    return out or dates


def _split_blocks(text: str) -> list[list[str]]:
    blocks, cur = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        if LINK_RE.match(line) and "Подписаться" in line:
            continue
        cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _strip_emoji(text: str) -> tuple[str, str]:
    m = HEADER_RE.match(text)
    emoji = (m.group("emoji") or "").strip()
    rest = m.group("rest").strip()
    return emoji, rest


def _clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip(" -–—:")


# ---------- разбор ----------

def parse_message(text: str, msg_date: datetime | date, source: str = "", message_id: str = "",
                  url: str | None = None) -> list[Event]:
    ref = msg_date.date() if isinstance(msg_date, datetime) else msg_date
    text = clean_markdown(EXPORT_HEADER_RE.sub("", text))
    dates = _message_dates(text, ref)
    if not dates:
        return []

    blocks = _split_blocks(text)
    if not blocks:
        return []

    # Общая цена для всего поста («🎟 Вход на все мероприятия бесплатный.»)
    global_price = None
    for b in blocks:
        if len(b) == 1 and PRICE_RE.match(b[0]):
            global_price = PRICE_RE.match(b[0]).group(1).strip()

    events: list[Event] = []
    announcement_blocks = [b for b in blocks if any(DATE_LINE_RE.match(l) or TIME_LINE_RE.match(l) for l in b)]

    if announcement_blocks:
        # Формат 3: одно событие на сообщение. Служебные строки могут быть
        # разбросаны по нескольким абзацам, собираем их со всего поста.
        emoji, title = _strip_emoji(blocks[0][0])
        title = _clean_title(title.rstrip("!"))
        address = price = None
        time_lines: list[str] = []
        marker_blocks = []
        for blk in blocks:
            if blk is blocks[0] or not any(MARKER_RE.match(l) for l in blk):
                continue
            marker_blocks.append(blk)
            for i, l in enumerate(blk):
                if (m := ADDRESS_RE.match(l)):
                    address = m.group(1).strip()
                    # улица строкой ниже: «📍 Где: Tor Partynice» + «ul. Zwycięska 2, Wrocław»
                    if i + 1 < len(blk) and not MARKER_RE.match(blk[i + 1]) and STREET_LINE_RE.match(blk[i + 1]):
                        address += ", " + blk[i + 1].strip()
                elif (m := PRICE_RE.match(l)):
                    price = m.group(1).strip()
                elif (m := TIME_LINE_RE.match(l)):
                    time_lines.append(m.group(1))
        if not address:
            return []
        desc_parts = []
        for blk in blocks:
            if blk is blocks[0] or blk in marker_blocks:
                continue
            desc_parts.append(" ".join(blk))
        description = "\n".join(desc_parts).strip()

        # повторяемость ищем только в служебных строках (⏰/🕘/📅), не в описании
        recurrence = None
        service_text = " ".join(" ".join(b) for b in marker_blocks)
        rec = _recurrence(" ".join(time_lines) + " " + " ".join(l for b in marker_blocks for l in b if DATE_LINE_RE.match(l)))
        if rec:
            weekdays, recurrence = rec
            dates = _expand_recurrence(dates, weekdays, service_text, ref)

        def time_for(d: date) -> tuple[str | None, str | None]:
            # несколько строк «⏰ Пятница - 16:00-01:00»: берём строку своего дня недели
            for tl in time_lines:
                for name, wd in WEEKDAYS.items():
                    if name in tl.lower() and wd == d.weekday():
                        return _parse_time(tl)
            return _parse_time(time_lines[0]) if time_lines else (None, None)

        for i, d in enumerate(dates, start=1):
            t_start, t_end = time_for(d)
            events.append(Event(date=d.isoformat(), title=title, address=address, time_start=t_start,
                                time_end=t_end, description=description, price=price or global_price,
                                emoji=emoji, source=source, message_id=message_id, url=url,
                                day_index=i, day_count=len(dates), recurrence=recurrence))
        return events

    # Форматы 1 и 2: много блоков с 📍
    for b in blocks:
        if not any(ADDRESS_RE.match(l) for l in b):
            continue
        header = b[0]
        if header.startswith(("📍", "🎟")):
            continue
        emoji, rest = _strip_emoji(header)
        t_start, t_end = None, None
        m = TIME_RANGE_RE.match(rest)
        if m:
            t_start, t_end = _parse_time(m.group(0))
            rest = rest[m.end():]
        title = _clean_title(rest)
        if not title:
            continue
        address = price = None
        desc = []
        for i, l in enumerate(b[1:], start=1):
            if (m := ADDRESS_RE.match(l)):
                address = m.group(1).strip()
                if i + 1 < len(b) and not MARKER_RE.match(b[i + 1]) and STREET_LINE_RE.match(b[i + 1]):
                    address += ", " + b[i + 1].strip()
                    b[i + 1] = "📍"  # уже учтено, в описание не попадёт
            elif l == "📍":
                continue
            elif (m := PRICE_RE.match(l)):
                price = m.group(1).strip()
            elif LINK_RE.match(l):
                continue
            else:
                desc.append(l)
        for i, d in enumerate(dates, start=1):
            events.append(Event(date=d.isoformat(), title=title, address=address, time_start=t_start,
                                time_end=t_end, description=" ".join(desc), price=price or global_price,
                                emoji=emoji, source=source, message_id=message_id, url=url,
                                day_index=i, day_count=len(dates)))
    return events


def split_export(text: str) -> list[dict]:
    """Текстовый экспорт вида «[10.09.2026 7:59] Канал: ...» -> список сообщений."""
    parts = []
    matches = list(EXPORT_HEADER_RE.finditer(text))
    if not matches:
        return [{"text": text, "date": datetime.now(), "source": "", "id": "0"}]
    for i, m in enumerate(matches):
        dd, mm, yyyy, hh, mi, src = m.groups()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        parts.append({
            "text": body,
            "date": datetime(int(yyyy), int(mm), int(dd), int(hh), int(mi)),
            "source": src.strip(),
            "id": f"{yyyy}{mm}{dd}{int(hh):02d}{mi}",
        })
    return parts


def _latin_share(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if ord(c) < 0x400) / len(letters)


def _norm_addr(a: str) -> str:
    a = a.lower()
    a = re.sub(r"^(старт|start)[^:\-–—]*[:\-–—]\s*", "", a)
    return re.sub(r"[^\w]+", " ", a).strip()


def dedupe(events: list[Event]) -> list[Event]:
    """Одно и то же событие из разных сообщений.

    Совпадение по (дата, адрес, время начала) считаем одним событием, даже если
    названия отличаются: анонс «Во Вроцлаве пройдет большой винтажный маркет
    Targi Jedwab» и строка дайджеста «Targi Jedwab». Название берём то, что
    больше похоже на имя собственное (больше латиницы, короче), описание -
    самое подробное, остальные поля - первое непустое.
    """
    groups: dict[tuple, list[Event]] = {}
    for e in events:
        # после геокодинга сравниваем точки (~100 м), до него - текст адреса
        place = (round(e.lat, 3), round(e.lon, 3)) if e.lat is not None else _norm_addr(e.address)
        k = (e.date, place, e.time_start)
        groups.setdefault(k, []).append(e)
    out: list[Event] = []
    for evs in groups.values():
        if len(evs) == 1:
            out.append(evs[0])
            continue
        # среди одинаковых названий оставляем одну запись, затем сводим разные
        by_title: dict[str, Event] = {}
        for e in evs:
            t = e.title.lower().strip()
            if t not in by_title or len(e.description) > len(by_title[t].description):
                by_title[t] = e
        cands = list(by_title.values())
        if len(cands) == 1:
            out.append(cands[0])
            continue
        title_src = max(cands, key=lambda x: (_latin_share(x.title), -len(x.title)))
        desc_src = max(cands, key=lambda x: len(x.description))
        merged = Event(**desc_src.to_dict())
        merged.title = title_src.title
        merged.emoji = title_src.emoji or merged.emoji
        for f in ("time_end", "price", "title_ru", "recurrence", "url"):
            if not getattr(merged, f):
                for c in cands:
                    if getattr(c, f):
                        setattr(merged, f, getattr(c, f))
                        break
        # нумерацию дней берём у самой длинной серии («2 из 3» важнее «1 из 1»)
        longest = max(cands, key=lambda x: x.day_count)
        merged.day_index, merged.day_count = longest.day_index, longest.day_count
        # другие названия сохраняем в описании, чтобы не потерять контекст
        others = [c.title for c in cands if c is not title_src and c.title.lower() != merged.title.lower()]
        if others:
            merged.description = (merged.description + "\n\n" + "Также: " + "; ".join(others)).strip()
        out.append(merged)
    return out
