"""When can you actually register? CU assigns enrollment appointments by completed credit hours.

Your exact appointment time is only visible inside Buff Portal (login required), so this module
gives you the *block* you fall in (the first appointment time of that block; individual
appointments are staggered every ~15 minutes) plus the other key dates. Live parsing of a
registrar "Continuing Student Enrollment Dates" post is supported with --enroll-url; built-in
data below was copied from the registrar's Spring 2027 post.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from .util import get_html

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class Block:
    label: str
    start: datetime
    min_credits: float = None       # None = not a credit-based block (priority, law, ...)


@dataclass
class Dates:
    term: str
    blocks: list
    open_enrollment: date = None
    cart_opens: date = None
    reservation: tuple = None       # (start date, end datetime)
    source: str = ""
    extras: list = field(default_factory=list)


def _dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi)


KNOWN = {
    "2271": Dates(
        term="Spring 2027",
        blocks=[
            Block("Priority registration (athletes / accommodations)", _dt(2026, 11, 2, 8, 30)),
            Block("Seniors (90+ credits)", _dt(2026, 11, 2, 9, 30), 90),
            Block("Juniors (60-89.99 credits)", _dt(2026, 11, 9, 8, 30), 60),
            Block("Sophomores (30-59.99 credits)", _dt(2026, 11, 11, 11, 30), 30),
            Block("Freshmen (0-7.99 credits)", _dt(2026, 11, 16, 8, 30), 0),
            Block("Freshmen (8-29.99 credits)", _dt(2026, 11, 16, 9, 30), 8),
        ],
        open_enrollment=date(2027, 1, 4),
        cart_opens=date(2026, 10, 12),
        reservation=(date(2026, 10, 26), _dt(2026, 10, 30, 17, 0)),
        source="https://www.colorado.edu/registrar/2026/09/28/spring-2027-enrl-dates",
        extras=["Graduate students Nov 5, 7:30am; law students Nov 20, 9am; incoming/readmitted students Nov 30."],
    ),
}

_DATE = re.compile(r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})")
_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*[–-]\s*(.+)$", re.I)
_CRED = re.compile(r"\((\d+(?:\.\d+)?)\s*(?:\+|[–-]\s*\d+(?:\.\d+)?)\s*credits?\)", re.I)


def parse_blocks(html):
    """Parse a registrar 'Continuing Student Enrollment Dates' post into Dates."""
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    blocks, day = [], None
    for raw in text.split("\n"):
        line = raw.strip(" \u2022*-\u00a0\t")
        m = _DATE.search(line)
        if m and len(line) < 60:
            day = (int(m[3]), MONTHS[m[1][:3].lower()], int(m[2]))
            continue
        t = _TIME.match(line)
        if day and t:
            h = int(t[1]) % 12 + (12 if t[3].lower() == "p" else 0)
            label = t[4].strip(" *")
            c = _CRED.search(label)
            blocks.append(Block(label, _dt(*day, h, int(t[2] or 0)), float(c[1]) if c else None))
    oe = re.search(r"open enrollment, which begins\s+(?:\w+day,\s+)?([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})", text)
    return Dates("", blocks, date(int(oe[3]), MONTHS[oe[1][:3].lower()], int(oe[2])) if oe else None)


def get_dates(db, url=None):
    if url:
        d = parse_blocks(get_html(url, ttl=3600))
        if d.blocks:
            d.source, d.term = url, d.term or db
            return d
        print("  ! couldn't find enrollment blocks on that page; using built-in dates if available")
    return KNOWN.get(db)


def block_for(credits, dates):
    """The credit-based block with the highest minimum that is <= your credits."""
    cands = [b for b in dates.blocks if b.min_credits is not None and b.min_credits <= credits]
    return max(cands, key=lambda b: b.min_credits) if cands else None


def _fmt(d):                                  # portable (no %-d, which breaks on Windows)
    if isinstance(d, datetime):
        return f"{d:%a %b} {d.day}, {d.year} {d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"
    return f"{d:%a %b} {d.day}, {d.year}"


def describe(dates, credits, now=None):
    now = now or datetime.now()
    fmt = _fmt
    out = [f"Registration for {dates.term}  (source: {dates.source})"]
    b = block_for(credits, dates)
    if b:
        days = (b.start - now).total_seconds() / 86400
        when = (f"opens in {days:.0f} day(s)" if days > 1 else "opens within a day" if days > 0
                else "is already open: you can register now")
        out.append(f"  Your block: {b.label}")
        out.append(f"    first appointment {fmt(b.start)} -> {when}.")
        out.append("    Your exact slot (staggered ~every 15 min) is on your Buff Portal enrollment-dates card.")
    else:
        out.append("  No credit-based block matched; check Buff Portal for your assigned appointment.")
    if dates.cart_opens:
        out.append(f"  Shopping cart: open since {fmt(dates.cart_opens)}. Build your cart ahead of time." if
                   dates.cart_opens <= now.date() else f"  Shopping cart opens {fmt(dates.cart_opens)}.")
    if dates.reservation:
        out.append(f"  Course reservations (if eligible): {fmt(dates.reservation[0])} through {fmt(dates.reservation[1])}.")
    if dates.open_enrollment:
        out.append(f"  Open enrollment (everyone): {fmt(dates.open_enrollment)}.")
    out += [f"  Also: {x}" for x in dates.extras]
    out.append("  Registration stays open once your appointment starts; earlier is better for popular sections.")
    return "\n".join(out)


def write_ics(path, dates, credits, appointment=None):
    """Calendar file with your registration start (+ reminders) and open enrollment."""
    b = block_for(credits, dates)
    start = appointment or (b.start if b else None)
    events = []
    if start:
        events.append(("Register for " + dates.term + " classes", start, start + timedelta(minutes=30), False))
    if dates.open_enrollment:
        events.append(("Open enrollment begins (" + dates.term + ")", dates.open_enrollment, None, True))
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//cu_planner//EN"]
    for i, (name, s, e, all_day) in enumerate(events):
        lines += ["BEGIN:VEVENT", f"UID:cu-planner-{stamp}-{i}", f"DTSTAMP:{stamp}", f"SUMMARY:{name}"]
        if all_day:
            lines.append("DTSTART;VALUE=DATE:" + s.strftime("%Y%m%d"))
        else:
            lines += ["DTSTART:" + s.strftime("%Y%m%dT%H%M%S"), "DTEND:" + e.strftime("%Y%m%dT%H%M%S")]
            for mins in (24 * 60, 15):
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{name}",
                          f"TRIGGER:-PT{mins}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return path
