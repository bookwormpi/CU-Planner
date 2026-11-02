"""Client for https://classes.colorado.edu (FOSE-style JSON API behind the search page)."""
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import quote

from .util import post_json

API = "https://classes.colorado.edu/api/?page=fose&route={}"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAYTOK = {"m": 0, "tu": 1, "t": 1, "w": 2, "th": 3, "r": 3, "f": 4, "sa": 5, "su": 6}
_TERM_DIGIT = {"spring": 1, "summer": 4, "fall": 7}


class ClassSearchUnavailable(RuntimeError):
    pass


def srcdb(term):
    """'Spring 2027' -> '2271' (CU term codes: 2 + YY + 1/4/7)."""
    if re.fullmatch(r"\d{4}", term.strip()):
        return term.strip()
    m = re.match(r"(spring|summer|fall)\s+(\d{4})", term.strip(), re.I)
    if not m:
        raise ValueError(f"Can't read term {term!r}; use e.g. 'Spring 2027' or a code like 2271")
    return f"2{int(m[2]) % 100:02d}{_TERM_DIGIT[m[1].lower()]}"


def term_name(code):
    season = {v: k for k, v in _TERM_DIGIT.items()}[int(code[3])].title()
    return f"{season} 20{code[1:3]}"


def previous_terms(code, n=4):
    """Previous n Fall/Spring terms before `code`."""
    out, yy, d = [], int(code[1:3]), int(code[3])
    while len(out) < n:
        d, yy = (1, yy) if d == 7 else (7, yy - 1) if d == 1 else (1, yy)
        if d != 4:
            out.append(f"2{yy:02d}{d}")
    return out


def _mins(s):
    s = s.lower().replace(" ", "")
    h, m = s[:-2].split(":")
    return (int(h) % 12 + (12 if s.endswith("pm") else 0)) * 60 + int(m)


_MEET = re.compile(r"([A-Za-z]+)\s+(\d{1,2}:\d{2}\s*[ap]m)\s*-\s*(\d{1,2}:\d{2}\s*[ap]m)", re.I)


def parse_meets(meets, meeting_times=None):
    """-> [(day 0=Mon, start_min, end_min)]. Prefers the JSON meetingTimes, falls back to text."""
    out = []
    if meeting_times:
        try:
            for mt in json.loads(meeting_times) if isinstance(meeting_times, str) else meeting_times:
                s, e = str(mt["start_time"]).zfill(4), str(mt["end_time"]).zfill(4)
                out.append((int(mt["meet_day"]), int(s[:2]) * 60 + int(s[2:]), int(e[:2]) * 60 + int(e[2:])))
        except (ValueError, KeyError, TypeError):
            out = []
    if not out and meets:
        for days, a, b in _MEET.findall(meets):
            for tok in re.findall(r"Su|Sa|Th|Tu|T|M|W|R|F", days, re.I):
                out.append((_DAYTOK[tok.lower()], _mins(a), _mins(b)))
    return sorted(set(out))


@dataclass
class Section:
    code: str
    title: str
    crn: str
    no: str
    kind: str                       # LEC / REC / LAB ...
    status: str
    cancelled: bool
    meets: list
    instructor: str
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def linked(self):
        """CRNs Class Search says must be registered together with this section."""
        return {x for x in re.split(r"[,\s]+", str(self.raw.get("linked_crns") or "")) if x}

    @property
    def open(self):
        return not self.cancelled and self.status.upper() == "A"

    def when(self):
        if not self.meets:
            return "no scheduled meetings (online/arranged)"
        by_time = defaultdict(list)
        for d, a, b in self.meets:
            by_time[(a, b)].append(DAYS[d])
        return "; ".join(f"{''.join(x[:2] for x in days)} {fmt_time(a)}-{fmt_time(b)}"
                         for (a, b), days in sorted(by_time.items()))


def fmt_time(m):
    h, mm = divmod(m, 60)
    return f"{(h % 12) or 12}:{mm:02d}{'a' if h < 12 else 'p'}"


def _section_from(r):
    return Section(
        code=r.get("code", "").strip(), title=r.get("title", ""), crn=str(r.get("crn", "")),
        no=str(r.get("no", "")), kind=(r.get("schd") or "LEC").upper(), status=str(r.get("stat", "")),
        cancelled=str(r.get("isCancelled", r.get("is_cancelled", ""))).strip().lower() in ("1", "y", "true")
        or str(r.get("stat", "")).upper() in ("C", "X"),
        meets=parse_meets(r.get("meets", ""), r.get("meetingTimes")),
        instructor=r.get("instr", ""), raw=r)


def _search(db, criteria, alias=None):
    url = API.format("search")
    if alias:
        url += f"&alias={quote(alias, safe='')}"
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://classes.colorado.edu",
        "Referer": "https://classes.colorado.edu/",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        data = post_json(url, {"other": {"srcdb": db}, "criteria": criteria},
                         encoded_body=True, headers=headers)
    except RuntimeError as exc:
        raise ClassSearchUnavailable(
            f"CU Class Search is temporarily unavailable for {term_name(db)} "
            f"({criteria[0].get('value', 'course search')}): {exc}"
        ) from exc
    if "Cannot open database" in str(data.get("fatal", "")):
        raise ClassSearchUnavailable(
            f"Class Search hasn't published {term_name(db)} yet, so suggestions use the catalog's usual "
            "Fall/Spring offerings instead of live sections.")
    if data.get("fatal"):
        raise RuntimeError(f"Class Search error: {data['fatal']}")
    return data.get("results", [])


def subject_sections(db, subject):
    """Every section returned for a subject alias, as Section objects."""
    rows = _search(db, [{"field": "alias", "value": subject}], alias=subject)
    return [_section_from(r) for r in rows if r.get("code", "").startswith(subject + " ")]


def course_sections(db, code):
    """Every section returned for an exact course-code alias."""
    rows = _search(db, [{"field": "alias", "value": code}], alias=code)
    return [_section_from(r) for r in rows if r.get("code", "").strip() == code]


def term_sections(db, codes):
    """{code: [Section]} for the requested courses."""
    out = {c: [] for c in codes}
    for c in codes:
        out[c] = course_sections(db, c)
    return out


def infer_offerings(codes, current_db, back=4):
    """{code: {'Fall','Spring'}} based on which of the last few terms had non-cancelled sections."""
    seen = defaultdict(set)
    for db in previous_terms(current_db, back):
        for code in codes:
            try:
                if any(not s.cancelled for s in course_sections(db, code)):
                    seen[code].add("Fall" if db[3] == "7" else "Spring")
            except Exception as exc:
                print(f"  ! couldn't read {code} for {term_name(db)}: {exc}")
    return dict(seen)
