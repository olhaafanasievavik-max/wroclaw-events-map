"""Геокодирование адресов через Nominatim (OpenStreetMap) с кэшем.

Правила Nominatim: не чаще одного запроса в секунду, обязательный User-Agent.
Результаты кладём в data/geocache.json, ручные правки в data/overrides.json
(ключ - адрес как в сообщении, значение - {"lat":..,"lon":..}).
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "wroclaw-map personal bot (contact via telegram)"
# Вроцлав и агломерация: lon_min, lat_max, lon_max, lat_min
VIEWBOX = "16.55,51.35,17.55,50.85"
STREET_RE = re.compile(r"\b(?:ul\.|pl\.|al\.|plac|ulica|aleja|bulwar|rondo|skwer|przejście|wyspa)\s+[^,;]+", re.I)
BARE_STREET_RE = re.compile(r"^[^\d,]{3,}\s\d+[\w/-]*$")  # «Wyspa Słodowa 7» без префикса
JUNK_RE = re.compile(r",?\s*(?:\d+\s*(?:этаж|piętro)|lokal\s*[\w.]+|piętro\s*\d+).*$", re.I)
PREFIX_RE = re.compile(r"^(?:старт(?:\s+программы)?\s*[-:–—]\s*|start\s*[-:]\s*|разные локации вроцлава,\s*старт программы\s*[-–—]\s*)", re.I)


class Geocoder:
    def __init__(self, data_dir: Path):
        self.cache_path = data_dir / "geocache.json"
        self.overrides_path = data_dir / "overrides.json"
        self.cache = self._load(self.cache_path)
        self.overrides = self._load(self.overrides_path)
        self._last_request = 0.0

    @staticmethod
    def _load(p: Path) -> dict:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {}

    def save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- кандидаты запросов, от точного к грубому ----
    # Возвращает пары (kind, text): kind = "street" -> структурированный запрос
    # street=<text>&city=Wrocław, kind = "free" -> свободный поиск по названию.
    @staticmethod
    def candidates(address: str) -> list[tuple[str, str]]:
        a = PREFIX_RE.sub("", address.strip())
        a = re.sub(r"\s+", " ", a)
        base = JUNK_RE.sub("", a).strip(" ,")
        base = re.sub(r",\s*Wrocław$", "", base, flags=re.I)
        out: list[tuple[str, str]] = []
        m = STREET_RE.search(base)
        street = None
        if not m:
            # последний сегмент вида «Wyspa Słodowa 7» считаем адресом
            for seg in reversed([s.strip() for s in base.split(",")]):
                if BARE_STREET_RE.match(seg):
                    street = seg
                    break
        if m:
            # «ul. Sienkiewicza 8A» -> «Sienkiewicza 8A»: Nominatim не любит префикс ul.
            street = re.sub(r"^(?:ul\.|pl\.|al\.|ulica|aleja|plac)\s+", "", m.group(0).strip(), flags=re.I)
            street = re.sub(r"\s+(?:и|i|oraz)\s+.*$", "", street)  # «Stawową, Piłsudskiego и Kościuszki»
        venue = base.split(",")[0].strip()
        venue_is_street = bool(m) and m.start() <= 1
        # Точный адрес с номером дома надёжнее названия площадки: по названию
        # Nominatim иногда находит одноимённый объект в другом районе.
        if street:
            out.append(("street", street))
            # 46C -> 46, 37/38 -> 37
            simpler = re.sub(r"(\d+)\s*[A-Za-z]$", r"\1", street)
            simpler = re.sub(r"(\d+)\s*/\s*\d+$", r"\1", simpler)
            if simpler != street:
                out.append(("street", simpler))
        if venue and not venue_is_street:
            out.append(("free", venue))          # Hala Stulecia, Plac Solny, Czeski Film
            # «Rynek и Ogród Staromiejski», «Pitlane Summer Bar - крыша Wroclavia»: по частям
            for part in re.split(r"\s+(?:и|i|oraz)\s+|\s+[-–—]\s+", venue):
                part = part.strip()
                if part and part != venue:
                    out.append(("free", part))
        if street:
            out.append(("street", re.sub(r"\s+\d+[\w/-]*$", "", street)))  # только улица, без номера
        # адрес в пригороде: «ul. Pierwoszowska 2, Wisznia Mała» -> ищем как есть, без «Wrocław»
        segs = [s.strip() for s in base.split(",")]
        town = next((s for s in reversed(segs) if s and not re.search(r"\d", s) and s != venue), None)
        if street and town:
            out.insert(0, ("street_in", f"{street}|{town}"))
        out.append(("raw", base))
        if re.search(r"lądowisko", base, re.I):   # в OSM аэродромы подписаны как Lotnisko
            out.append(("raw", re.sub(r"lądowisko", "Lotnisko", base, flags=re.I)))
        for part in re.split(r"\s*[-–—/]\s*", venue):
            # «Wrocław-Szymanów» -> «Szymanów»; куски со словом Wrocław слишком общие
            if part and part != venue and len(part) > 3 and "wroc" not in part.lower():
                out.append(("raw", part))
        if not out:
            out.append(("free", base))
        uniq: list[tuple[str, str]] = []
        for c in out:
            c = (c[0], c[1].strip(" ,.-"))
            if c[1] and c not in uniq:
                uniq.append(c)
        return uniq

    def _query(self, kind: str, q: str) -> tuple[float, float] | None:
        wait = 1.1 - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        params = {"format": "json", "limit": 1, "viewbox": VIEWBOX, "bounded": 1, "accept-language": "pl"}
        if kind == "street":
            params.update({"street": q, "city": "Wrocław"})
        elif kind == "street_in":
            street, town = q.split("|", 1)
            params.update({"street": street, "city": town})
        elif kind == "raw":
            params["q"] = q
        else:
            params["q"] = f"{q}, Wrocław" if "wroc" not in q.lower() else q
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        self._last_request = time.time()
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # сеть, таймаут
            print(f"  ! nominatim error for {q!r}: {e}")
            return None
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])

    def geocode(self, address: str) -> tuple[float, float, str] | None:
        """-> (lat, lon, запрос_который_сработал) или None."""
        if address in self.overrides:
            o = self.overrides[address]
            return o["lat"], o["lon"], "override"
        if address in self.cache:
            c = self.cache[address]
            return (c["lat"], c["lon"], c["q"]) if c else None
        for kind, q in self.candidates(address):
            res = self._query(kind, q)
            if res:
                self.cache[address] = {"lat": res[0], "lon": res[1], "q": q}
                self.save()
                return res[0], res[1], q
        self.cache[address] = None
        self.save()
        return None
