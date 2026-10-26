"""Turn spoken date/time phrases ("next Thursday at 3pm", "tomorrow morning")
into concrete datetimes relative to the meeting's current time."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY_RE = r"(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


@dataclass
class ParsedWhen:
    day: date | None = None
    at: time | None = None
    # Soft window when only a part of day was said ("Thursday morning").
    window: tuple[time, time] | None = None
    text: str = ""

    @property
    def has_day(self) -> bool:
        return self.day is not None


def _weekday_index(token: str) -> int:
    token = token.lower()
    for i, name in enumerate(WEEKDAYS):
        if name.startswith(token[:3]):
            return i
    raise ValueError(token)


def parse_day(text: str, now: datetime) -> tuple[date | None, str]:
    t = text.lower()
    today = now.date()

    if re.search(r"\btoday\b|\blater today\b", t):
        return today, "today"
    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2), "day after tomorrow"
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1), "tomorrow"

    m = re.search(r"\bnext week\b(?:\s+on)?\s+" + _WEEKDAY_RE + r"\b", t) or re.search(
        r"\b" + _WEEKDAY_RE + r"\b\s+next week\b", t
    )
    if m:
        target = _weekday_index(m.group(1))
        next_monday = today + timedelta(days=7 - today.weekday())
        return next_monday + timedelta(days=target), m.group(0)

    m = re.search(r"\bnext\s+" + _WEEKDAY_RE + r"\b", t)
    if m:
        target = _weekday_index(m.group(1))
        delta = (target - today.weekday()) % 7 or 7
        # "next Thursday" said on Monday usually still means this coming Thursday;
        # we follow the common reading: the first one strictly after today.
        return today + timedelta(days=delta), m.group(0)

    m = re.search(r"\b(?:this\s+|on\s+)?" + _WEEKDAY_RE + r"\b", t)
    if m:
        target = _weekday_index(m.group(1))
        delta = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=delta), m.group(0).strip()

    if re.search(r"\bnext week\b", t):
        return today + timedelta(days=7 - today.weekday()), "next week"
    return None, ""


def parse_time(text: str) -> tuple[time | None, tuple[time, time] | None]:
    t = text.lower()
    if re.search(r"\bnoon\b|\blunch\s*time\b", t):
        return time(12, 0), None

    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b", t)
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if hour > 12 or minute > 59:
            return None, None
        if ampm.startswith("p") and hour != 12:
            hour += 12
        if ampm.startswith("a") and hour == 12:
            hour = 0
        return time(hour, minute), None

    m = re.search(r"\b(?:at\s+)([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return time(int(m.group(1)), int(m.group(2))), None

    m = re.search(r"\bat\s+(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")(?:\s+o'?clock)?\b", t)
    if m:
        raw = m.group(1)
        hour = int(raw) if raw.isdigit() else _WORD_NUMBERS[raw]
        if 1 <= hour <= 12:
            # Business-hours guess: "at 3" means 3pm, "at 9" means 9am.
            if hour < 8:
                hour += 12
            return time(hour, 0), None

    if re.search(r"\bmorning\b", t):
        return None, (time(9, 0), time(12, 0))
    if re.search(r"\bafternoon\b", t):
        return None, (time(13, 0), time(17, 0))
    if re.search(r"\b(evening|end of (the )?day|eod)\b", t):
        return None, (time(16, 0), time(18, 0))
    return None, None


def parse_when(text: str, now: datetime) -> ParsedWhen:
    day, day_text = parse_day(text, now)
    at, window = parse_time(text)
    return ParsedWhen(day=day, at=at, window=window, text=day_text)


def parse_deadline(text: str, now: datetime) -> date | None:
    """Deadline phrases for tasks: "by Friday", "by end of week", "before Monday"."""
    t = text.lower()
    if re.search(r"\b(by|before)\s+(the\s+)?end of (the\s+)?week\b|\bby eow\b", t):
        return now.date() + timedelta(days=(4 - now.weekday()) % 7)
    if re.search(r"\b(by|before)\s+(the\s+)?end of (the\s+)?day\b|\bby eod\b|\btonight\b", t):
        return now.date()
    m = re.search(r"\b(by|before|until|due)\b(.*)", t)
    if m:
        day, _ = parse_day(m.group(2), now)
        return day
    day, _ = parse_day(t, now)
    return day
