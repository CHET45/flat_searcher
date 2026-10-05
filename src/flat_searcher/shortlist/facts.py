"""Facts the listing text states outright, found by regex instead of by a model."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MAX_EVIDENCE_CHARS = 160

_ROOM = (
    r"(?<!vannas )(?<!ванная )(?<!ванной )"
    r"(?:\w*istab\w*|комнат\w*|гостин\w*|спальн\w*)"
)
_NOT = r"(?<!\bne )(?<!\bnav )(?<!\bне )"
_WALKTHROUGH_WORD = r"(?:caurstaigājam\w*|caurejam\w*|проходн\w*|смежн\w*)"
_ISOLATED_WORD = r"(?:izolēt\w*|изолированн\w*)"
_GAP = r"[^.;!?]"
_ROOMS = r"(?:\w*istabas|комнаты)"
_NOT_KITCHEN = r"(?!\s+(?:virtuv|кухн))"

_WALKTHROUGH = re.compile(
    rf"{_ROOM}{_GAP}{{0,25}}?{_NOT}\b{_WALKTHROUGH_WORD}"
    rf"|{_NOT}\b{_WALKTHROUGH_WORD}{_GAP}{{0,15}}?{_ROOM}"
)
_ISOLATED = re.compile(
    rf"\b{_ISOLATED_WORD}\s+(?:\w+\s+)?{_ROOM}"
    rf"|{_ROOMS}\s+(?:[\w,]+\s+){{0,3}}{_ISOLATED_WORD}{_NOT_KITCHEN}"
)

SERIES_PRIOR: dict[str, str] = {
    "Hrušč.": "walkthrough",
    "602.": "isolated",
    "119.": "isolated",
    "LT proj.": "isolated",
}

LAYOUT_RANK: dict[tuple[str, str], int] = {
    ("isolated", "text"): 4,
    ("isolated", "series"): 3,
    ("unknown", "none"): 2,
    ("n/a", "none"): 2,
    ("walkthrough", "series"): 1,
    ("walkthrough", "text"): 0,
}

_STOVE_HEATING = re.compile(
    r"\bkrā(?:sns|šņu)\s+apkur\w*"
    r"|\bapkur\w*\s*[-–:]?\s*(?:ar\s+)?(?:malkas\s+)?krāsn\w*"
    r"|\bmalkas\s+apkur\w*"
    r"|\bapkur\w*\s*[-–:]?\s*(?:ar\s+)?malk\w*"
    r"|\bпечн\w*\s+отоплени\w*"
    r"|\bдровян\w*\s+отоплени\w*"
    r"|\bотоплени\w*\s*[-–:]?\s*(?:печн\w*|печ(?:ь|ью|ами)\b|(?:на\s+)?дров\w*)"
)
_STOVE_HEATING_MORE = re.compile(
    r"\bkurin\w*\s+(?:\w+\s+){0,2}krāsn\w*|\bkrāsn\w*\s+kurin\w*"
    r"|\bтоп\w*\s+печ\w*|\bпеч\w*\s+для\s+отоплени\w*"
    r"|\bдровян\w*\s+печ\w*[^.;]{0,40}отоплени\w*|\bотоплени\w*[^.;]{0,60}\bдровян\w*\s+печ\w*"
    r"|\bstove\s+heating|\bheated\s+(?:by|with)\s+(?:a\s+)?(?:wood\s+)?stove"
    r"|\bwood(?:-burning)?\s+stove[^.;]{0,50}\bheating|\bheating\b[^.;]{0,60}\bwood(?:-burning)?\s+stove"
    r"|\bheating\b[^.;]{0,30}\bfirewood|\bheating\b[^.;]{0,40}\b(?:by|with)\s+(?:a\s+)?stoves?\b"
)
_STOVE_INSTALLABLE = re.compile(
    r"(?:possibility|option|iespēj\w*|можно|возможн\w*)\s+(?:to\s+)?(?:install|uzstādīt|ierīkot|установить|поставить)"
    r"\s+(?:\S+\s+){0,2}$"
)
_HEATING_SOURCES: dict[str, re.Pattern[str]] = {
    "house_boiler": re.compile(
        r"\b(?:centrāl|centralizēt)\w*\s+gāzes\s+apkur\w*"
        r"|\b(?:mājas|ēkas|savs?|sava|savu)\s+katlu\s*māj\w*"
        r"|\bpagrabā[^.;]{0,30}\bkatl\w*|\bkatl\w*[^.;]{0,40}\bmājas\s+(?:pirmajā\s+stāvā|pagrabā)"
        r"|\b(?:собственн|сво|домов)\w*\s+котельн\w*"
        r"|\bcommunal\s+(?:gas\s+)?boilers?|\bboiler\s+room\s+in\s+the\s+building"
    ),
    "own_boiler": re.compile(
        r"\b(?:autonom|individuāl)\w*\s+(?:gāzes\s+)?apkur\w*\b(?!\s+(?:siltuma\s+)?(?:skaitīt|uzskait|patēriņ))"
        r"|\b(?:автономн|индивидуальн)\w*\s+(?:газов\w*\s+)?отоплени\w*"
        r"|\bgāzes\s+apkur\w*|\bгазов\w*\s+отоплени\w*"
        r"|\b(?:individual|autonomous|own)\s+(?:gas\s+)?heating\b(?!\s+(?:meters?|metering|costs?))"
        r"|\bgas\s+heating"
    ),
    "heat_pump": re.compile(r"\bsiltumsūkn\w*|\bтеплов\w*\s+насос\w*|\bтеплонасос\w*|\bheat\s+pumps?\b"),
    "electric": re.compile(
        r"\belektrisk\w*\s+(?:apkur|radiator|sildītāj|konvektor)\w*|\belektroradiator\w*"
        r"|\bэлектрическ\w*\s+(?:отоплени|радиатор|конвектор|батаре)\w*|\bэлектро(?:батаре|конвектор)\w*"
        r"|\belectric\s+(?:heating|radiators?|heaters?|convectors?)"
    ),
    "district": re.compile(
        r"\b(?:centrāl|centralizēt)\w*\s+(?:pilsētas\s+)?apkur\w*|\bpilsētas\s+(?:centrālā\s+)?apkur\w*"
        r"|\bapkur\w*\s*[-–:]?\s*(?:centrāl|centralizēt)\w*|\brīgas\s+siltum\w*"
        r"|\bцентральн\w*\s+(?:городск\w*\s+)?отоплени\w*|\bгородск\w*\s+отоплени\w*"
        r"|\bотоплени\w*\s*[-–:]?\s*центральн\w*|\bцентральн\w*\s+теплоснабжени\w*"
        r"|\bcentral(?:ised|ized)?\s+heating|\bdistrict\s+heating|\bcity\s+heating"
    ),
}
_HEATING_BOILER = re.compile(
    r"\b(?:gāzes|granulu|cietā\s+kurināmā|apkures|elektro)\s*(?:apkures\s+)?katl\w*"
    r"|\bгазов\w*\s+котл\w*|\bкотл\w*\s+(?:на\s+газ\w*|отоплени\w*)|\bgas\s+boilers?"
)
HEATING_ORDER = ("house_boiler", "own_boiler", "heat_pump", "electric", "district")
_HEAT_WORD = re.compile(r"\bapkur\w*|\bapsild\w*|\bотоплени\w*|\bотаплива\w*|\bheating\b")
_HOT_WATER_WORD = re.compile(r"\bkarst\w*\s+ūden\w*|\bsilt\w*\s+ūden\w*|\bгоряч\w*\s+вод\w*|\bhot\s+water")
_RELATIVE = re.compile(r"\s*,\s*(?:kas|kurš|kura|kuru|который|которая|которое|that|which)\b")
_MODAL = re.compile(
    r"\b(?:iespēj\w*|var|varēs|можно|возможн\w*|options?|possibility|potential)\b[^.;]{0,25}?"
    r"\b(?:likt|uzlikt|uzstādīt|ierīkot|izbūvēt|veidot|pieslēgt|install\w*|установ\w*|постав\w*|подключ\w*)"
    r"|\b(?:could|can|may)\s+(?:also\s+)?be\s+(?:installed|added|connected)"
    r"|\buzstādīšan\w*|\bдля\s+установки|\bfor\s+(?:the\s+)?installation|\bpotenciāl\w*"
)
_CLAUSES = re.compile(r"[^.;!?\n]+[.;!?]?")

_HOT_WATER_CENTRAL = re.compile(
    r"\b(?:centralizēt|centrāl|pilsētas)\w*\s+(?:pilsētas\s+)?(?:aukst\w*\s*(?:un|/|,)\s*)?karst\w*\s+ūden\w*"
    r"|\b(?:centralizēt|centrāl)\w*\s+(?:pilsētas\s+)?apkur\w*\s+un\s+karst\w*\s+ūden\w*"
    r"|\bkarst\w*\s+ūden\w*\s*[-–:]?\s*(?:rīgas|pilsētas|centrāl|centralizēt)\w*"
    r"|\bkarst\w*\s+ūden\w*[^.;]{0,40}\bcentralizēt\w*"
    r"|\b(?:центральн|централизованн|городск)\w*\s+горяч\w*\s+вод\w*|\bгоряч\w*\s+вод\w*\s*[-–:]?\s*центральн\w*"
    r"|\bцентральн\w*\s+теплоснабжени\w*[^.;]{0,60}\bгоряч\w*\s+вод\w*"
    r"|\bcentral(?:ised|ized)?\s+hot\s+water"
)
_HOT_WATER_GAS = re.compile(r"\bgāzes\s+kolonk\w*|\bгазов\w*\s+колонк\w*|\bgas\s+water\s+heater")
_HOT_WATER_BOILER = re.compile(
    r"(?<!nav )\bboiler(?:is|a|i|u|im|iem|ī)\b|(?<!без )\bбойлер\w*"
    r"|\būdens\s+sild\w*|\bводонагрев\w*|\bwater\s+heater|\bhot\s+water\s+boilers?\b"
    r"|(?<!\bno )(?<!gas )(?<!heating )(?<!gas-based )\bboilers?\b(?=[^.;,]{0,25}\bhot\s+water)"
)
_SHARED_BOILER = re.compile(r"(?:centraliz\w*|centrāl\w*|mājas|ēkas|communal|домов\w*|общ\w*)\s+$")
HOT_WATER_ORDER = ("gas", "boiler", "central")
_STOVE_OBJECT = re.compile(
    r"(?<!mikroviļņu )(?<!mikroviļnu )(?<!микроволновая )(?<!духовая )"
    r"\b(?:krāsn\w*|печь|печка|печи)\b|\bwood(?:-burning)?\s+stove"
)
_REMOVED = re.compile(r"demontē\w*|\bbijusi\b|\bбыло\b|\bбыла\b|демонтир\w*")

_SHARE_SALE = re.compile(
    r"\b(?:pārdots|pārdod\w*|reģistrēts)\s+kā\s+domājam\w*\s+daļ\w*"
    r"|\bпрода[её]тся\s+как\s+(?:идеальн\w*\s+)?дол\w*"
)

_LAND = r"(?:\bzem(?:e|es|i|ei|ē)\b|\bzemesgabal\w*|\bземл\w*|\bземельн\w*)"
_LEASE = r"(?:\bnom(?!ain|aks)[aāu]\w*|\bаренд\w*)"
_CLAUSE = r"[^.!?\n,;()]"
_NEGATED_LEASE = re.compile(
    rf"(?:\bnav|\bbez|\bnebūs|\bnevis|\bнет|\bбез|\bникак\w*"
    rf"|\bне\s+(?:надо|нужно|придется|придётся))\b{_CLAUSE}{{0,40}}{_LEASE}"
    rf"|\bне\s+(?:в\s+)?{_LEASE}"
    rf"|{_LEASE}{_CLAUSE}{{0,12}}\b(?:nav|нет)\b"
)
_LEASED_LAND = re.compile(
    rf"{_LAND}[^.!?\n]{{0,60}}{_LEASE}|{_LEASE}[^.!?\n]{{0,60}}{_LAND}"
    rf"|{_LAND}[^.!?\n]{{0,30}}\b(?:nav|не)\s+(?:\w+\s+)?(?:īpašum|собственн)\w*"
    rf"|{_LAND}[^.!?\n]{{0,20}}\b(?:daļēji\s+īpašum|частично\s+в\s+собственн)\w*"
    r"|\bzemes\s+lietošanas\s+maks\w*|\bdenacionaliz\w*|\bденационализ\w*"
)
_OWNED_LAND = re.compile(
    rf"{_LAND}[^.!?\n]{{0,40}}(?<!nav )(?<!не в )(?<!не )\b(?:kop)?(?:īpašum\w*|собственн\w*)"
    rf"|{_LAND}[^.!?\n]{{0,20}}\bpieder\b"
)

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True)
class Fact:
    value: str
    source: str
    evidence: tuple[str, ...] = ()


def layout_fact(record: Mapping[str, Any]) -> Fact:
    rooms = (record.get("core") or {}).get("declared_rooms")
    if rooms is not None and rooms != 2:
        return Fact("n/a", "none")
    text = _text(record)
    walkthrough = _evidence(_WALKTHROUGH, text)
    if walkthrough:
        return Fact("walkthrough", "text", walkthrough)
    isolated = _evidence(_ISOLATED, text)
    if isolated:
        return Fact("isolated", "text", isolated)
    series = _series(record)
    prior = SERIES_PRIOR.get(series)
    if prior:
        return Fact(prior, "series", (series,))
    return Fact("unknown", "none")


def heating_fact(record: Mapping[str, Any]) -> Fact:
    """The heating system; a specific source beats the word "central", and "stove" only when nothing else is named."""
    clauses = _clauses(_text(record))
    found: dict[str, list[str]] = {kind: [] for kind in HEATING_ORDER}
    for clause in clauses:
        kind = _heating_kind(clause)
        if kind:
            found[kind].append(clause)
    kind = next((kind for kind in HEATING_ORDER if found[kind]), None)
    if kind:
        return Fact(kind, "text", tuple(dict.fromkeys(map(_quote, found[kind])))[:2])
    stove = [clause for clause in clauses if _stove_heating(clause)]
    if stove:
        return Fact("stove", "text", tuple(dict.fromkeys(map(_quote, stove)))[:2])
    return Fact("unknown", "none")


def stove_fact(record: Mapping[str, Any]) -> Fact:
    """Stove heating even beside another system; otherwise whether a stove is there at all."""
    text = _text(record)
    stove = [clause for clause in _clauses(text) if _stove_heating(clause)]
    if stove:
        return Fact("heating", "text", tuple(dict.fromkeys(map(_quote, stove)))[:2])
    present = _evidence(_STOVE_OBJECT, text)
    return Fact("present", "text", present) if present else Fact("none", "none")


def hot_water_fact(record: Mapping[str, Any]) -> Fact:
    """A local source beats a general claim of central hot water."""
    found: dict[str, list[str]] = {kind: [] for kind in HOT_WATER_ORDER}
    for clause in _clauses(_text(record)):
        kind = _hot_water_kind(clause)
        if kind:
            found[kind].append(clause)
    kind = next((kind for kind in HOT_WATER_ORDER if found[kind]), None)
    if kind is None:
        return Fact("unknown", "none")
    return Fact(kind, "text", tuple(dict.fromkeys(map(_quote, found[kind])))[:2])


def _hot_water_kind(clause: str) -> str | None:
    if _HOT_WATER_GAS.search(clause):
        return "gas"
    if any(
        _HOT_WATER_WORD.search(_near(clause, boiler))
        and not _SHARED_BOILER.search(clause[max(0, boiler.start() - 20) : boiler.start()])
        for boiler in _HEATING_BOILER.finditer(clause)
        if "gāz" in boiler.group() or "газ" in boiler.group() or "gas" in boiler.group()
    ):
        return "gas"
    if _HOT_WATER_BOILER.search(clause):
        return "boiler"
    if _HOT_WATER_CENTRAL.search(clause):
        return "central"
    return None


def _stove_heating(clause: str) -> bool:
    if _REMOVED.search(clause):
        return False
    return any(
        not _STOVE_INSTALLABLE.search(clause[: match.start()])
        for pattern in (_STOVE_HEATING, _STOVE_HEATING_MORE)
        for match in pattern.finditer(clause)
    )


def _heating_kind(clause: str) -> str | None:
    if _MODAL.search(clause):
        return None
    for kind in HEATING_ORDER:
        if _HEATING_SOURCES[kind].search(clause):
            return kind
        if kind == "own_boiler" and any(
            not _for_hot_water_only(clause, boiler) for boiler in _HEATING_BOILER.finditer(clause)
        ):
            return kind
    return None


def _for_hot_water_only(clause: str, match: re.Match[str]) -> bool:
    near = _near(clause, match)
    return bool(_HOT_WATER_WORD.search(near)) and not _HEAT_WORD.search(near)


def _near(clause: str, match: re.Match[str]) -> str:
    """The words that qualify a match: its comma part, plus a relative clause right after it."""
    before = clause[: match.start()].rsplit(",", 1)[-1][-50:]
    after = clause[match.end() : match.end() + 60]
    relative = _RELATIVE.match(after)
    if relative:
        after = after[: relative.end()] + after[relative.end() :].split(",", 1)[0]
    else:
        after = after.split(",", 1)[0]
    return before + match.group() + after


def _clauses(text: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSES.findall(text) if clause.strip()]


def _quote(clause: str) -> str:
    if len(clause) <= MAX_EVIDENCE_CHARS:
        return clause
    return clause[: MAX_EVIDENCE_CHARS - 1].rstrip() + "…"


def sells_share(record: Mapping[str, Any]) -> Fact:
    evidence = _evidence(_SHARE_SALE, _text(record))
    return Fact("yes", "text", evidence) if evidence else Fact("no", "none")


def land_fact(record: Mapping[str, Any]) -> Fact:
    """Leased wins: a flat whose land is even partly leased is harder to mortgage."""
    text = _text(record)
    leased = _evidence(_LEASED_LAND, text, mask=_NEGATED_LEASE)
    if leased:
        return Fact("leased", "text", leased)
    owned = _evidence(_OWNED_LAND, text)
    if owned:
        return Fact("owned", "text", owned)
    return Fact("unknown", "none")


def _series(record: Mapping[str, Any]) -> str:
    core = record.get("core") or {}
    fields = record.get("fields") or {}
    return str(core.get("building_series") or fields.get("Sērija") or "")


def _text(record: Mapping[str, Any]) -> str:
    text = record.get("text") or {}
    fields = record.get("fields") or {}
    parts = [str(text.get("title") or ""), str(text.get("description") or "")]
    parts.extend(str(value) for value in fields.values())
    return " \n".join(" ".join(part.split()) for part in parts).casefold()


def _evidence(
    pattern: re.Pattern[str],
    text: str,
    skip: re.Pattern[str] | None = None,
    mask: re.Pattern[str] | None = None,
) -> tuple[str, ...]:
    found: list[str] = []
    for sentence in _SENTENCE.findall(text):
        searched = mask.sub(lambda hit: " " * len(hit.group()), sentence) if mask else sentence
        match = pattern.search(searched)
        if not match or (skip is not None and skip.search(sentence)):
            continue
        snippet = sentence.strip()
        if len(snippet) > MAX_EVIDENCE_CHARS:
            start = max(0, match.start() - MAX_EVIDENCE_CHARS // 2)
            snippet = "…" + sentence[start : start + MAX_EVIDENCE_CHARS - 2].strip() + "…"
        if snippet not in found:
            found.append(snippet)
        if len(found) == 2:
            break
    return tuple(found)
