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
_OTHER_HEATING = re.compile(
    r"\bcentrāl\w*\s+apkur\w*|\bapkur\w*\s*[-–:]?\s*centrāl\w*"
    r"|\bgāzes\s+(?:apkur|katl)\w*|\belektrisk\w*\s+apkur\w*|\bsiltumsūkn\w*"
    r"|\bцентральн\w*\s+отоплени\w*|\bотоплени\w*\s*[-–:]?\s*центральн\w*"
    r"|\bгазов\w*\s+(?:отоплени|котл)\w*|\bэлектрическ\w*\s+отоплени\w*"
    r"|\bтеплов\w*\s+насос\w*"
)
_STOVE_OBJECT = re.compile(
    r"(?<!mikroviļņu )(?<!mikroviļnu )(?<!микроволновая )(?<!духовая )"
    r"\b(?:krāsn\w*|печь|печка|печи)\b"
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
    text = _text(record)
    other = _evidence(_OTHER_HEATING, text)
    stove = _evidence(_STOVE_HEATING, text, skip=_REMOVED)
    if other:
        return Fact("other", "text", other)
    if stove:
        return Fact("stove", "text", stove)
    mention = _evidence(_STOVE_OBJECT, text)
    if mention:
        return Fact("mention", "text", mention)
    return Fact("unknown", "none")


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
