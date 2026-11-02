"""Scrapes catalog.colorado.edu (CourseLeaf): programs, courses, prerequisites."""
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus, unquote, urljoin

from bs4 import BeautifulSoup

from .util import get_html

BASE = "https://catalog.colorado.edu"
CODE = re.compile(r"\b([A-Z]{2,4})[\s\u00a0]*(\d{4})\b")
NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def norm_code(text):
    m = CODE.search(unquote(text).replace("+", " ").replace("\u00a0", " "))
    return f"{m[1]} {m[2]}" if m else None


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


# --------------------------------------------------------------------------
# Prerequisite expressions:  ("c", code) | ("and", [..]) | ("or", [..]) | None
# --------------------------------------------------------------------------
_TOK = re.compile(r"\(|\)|\band\b|\bor\b|\b[A-Z]{2,4}\s?\d{4}\b|\b\d{4}\b")


def parse_expr(text):
    """'(A or B) and (C or D)'. 'or' binds tighter than 'and', which matches how
    the catalog writes unparenthesised lists ('A or B and C or D')."""
    toks, prefix = [], None
    for m in _TOK.finditer(text):
        s = m.group(0)
        if s in ("(", ")", "and", "or"):
            toks.append(s)
        elif re.fullmatch(r"\d{4}", s):
            if prefix:                       # 'CSCI 2270 and 2824' shorthand
                toks.append(("c", f"{prefix} {s}"))
        else:
            c = norm_code(s)
            prefix = c.split()[0]
            toks.append(("c", c))
    pos = 0

    def atom():
        nonlocal pos
        if pos < len(toks) and toks[pos] == "(":
            pos += 1
            e = conj()
            if pos < len(toks) and toks[pos] == ")":
                pos += 1
            return e
        if pos < len(toks) and isinstance(toks[pos], tuple):
            pos += 1
            return toks[pos - 1]
        return None

    def collapse(kind, items):
        items = [i for i in items if i]
        if not items:
            return None
        return items[0] if len(items) == 1 else (kind, items)

    def disj():
        nonlocal pos
        items = [atom()]
        while pos < len(toks) and toks[pos] == "or":
            pos += 1
            items.append(atom())
        return collapse("or", items)

    def conj():
        nonlocal pos
        items = [disj()]
        while pos < len(toks) and toks[pos] == "and":
            pos += 1
            items.append(disj())
        return collapse("and", items)

    out = []
    while pos < len(toks):
        before = pos
        out.append(conj())
        if pos == before:
            pos += 1
    return collapse("and", out)


def expr_codes(e):
    if not e:
        return []
    if e[0] == "c":
        return [e[1]]
    return [c for sub in e[1] for c in expr_codes(sub)]


def satisfied(e, have):
    if not e:
        return True
    if e[0] == "c":
        return e[1] in have
    return (all if e[0] == "and" else any)(satisfied(s, have) for s in e[1])


def and_groups(e):
    """Top-level AND terms; each one must be satisfied independently."""
    if not e:
        return []
    if e[0] != "and":
        return [e]
    return [g for sub in e[1] for g in and_groups(sub)]    # nested ANDs are still independent requirements


# --------------------------------------------------------------------------
# Courses
# --------------------------------------------------------------------------
@dataclass
class Course:
    code: str
    title: str
    credits: float
    desc: str = ""
    prereq: object = None
    coreq: object = None
    concurrent_ok: bool = False          # "prerequisite or corequisite"
    restrictions: list = field(default_factory=list)
    offered: set = None                  # {'Fall','Spring','Summer'} if the catalog says
    equiv: list = field(default_factory=list)   # "Equivalent - Duplicate Degree Credit Not Granted" courses
    companion: tuple = ()                # lab/recitation course normally taken in the same term


# Courses CU treats as the same content, so either one satisfies prerequisites and requirements.
EQUIV = [("APPM 1350", "MATH 1300"), ("APPM 1360", "MATH 2300"), ("APPM 2350", "MATH 2400")]
_EQ = {a: b for pair in EQUIV for a, b in (pair, pair[::-1])}


_EQ_CATALOG = {}      # filled from each subject page's "Equivalent - Duplicate Degree Credit Not Granted" lines
_TITLES = {}
_GENERIC = {"introduction", "to", "for", "and", "the", "of", "in", "general", "special", "topics", "engineering",
            "engineers", "science", "sciences", "applied", "fundamentals", "principles", "honors", "seminar"}


def _same_subject(a, b):
    """The catalog's duplicate-credit lists are broad (ECON 1088 'duplicates' Calculus 1); only trust pairs
    whose titles show the same subject matter."""
    words = lambda c: {w for w in re.findall(r"[a-z]+", _TITLES.get(c, "").lower())} - _GENERIC
    return bool(words(a) & words(b))


def with_equivalents(codes):
    """Return `codes` plus every equivalent course (APPM 2360 = MATH 2130 + MATH 3430)."""
    out = set(codes)
    out |= {_EQ[c] for c in codes if c in _EQ}
    for c in list(out):
        out |= {o for o in _EQ_CATALOG.get(c, ()) if _same_subject(c, o)}
    if "APPM 2360" in out:
        out |= {"MATH 2130", "MATH 3430"}
    if {"MATH 2130", "MATH 3430"} <= out:
        out.add("APPM 2360")
    return out


_TITLE = re.compile(r"^([A-Z]{2,4})\s*(\d{4})\s*\(([\d.]+)(?:\s*[-–]\s*[\d.]+)?\)\s*(.*)$")
_CLAUSE = re.compile(
    r"Requires\s+(?:an?\s+)?(pre-?requisites?|co-?requisites?)(\s+or\s+co-?requisites?)?(.*?)"
    r"(?=Requires\s|Restricted\s|$)", re.S | re.I)


_BODY = re.compile(r"Requires\s+(.*?)(?=Requires\s|Restricted\s|$)", re.S | re.I)
# "prerequisite course of", "prerequsite courses of" (sic), "prerequisite or corequisite course of", ...
_MARK = re.compile(r"\b(pre-?req\w*|co-?req\w*)(\s+or\s+co-?req\w*)?\s*(?:courses?\s*)?(?:of\s+)?", re.I)
_GRADE = re.compile(r"\([^)]*(?:grade|minimum)[^)]*\)", re.I)


def _segments(body):
    """[(kind, expr, connector_to_next)] where kind is 'pre', 'co' or 'pc' (prerequisite or corequisite)."""
    marks = list(_MARK.finditer(body))
    out = []
    for i, m in enumerate(marks):
        last = i == len(marks) - 1
        text = body[m.end():marks[i + 1].start() if not last else len(body)]
        if last:
            text = re.split(r"\((?:all|both|each|min)", text, maxsplit=1, flags=re.I)[0]
        else:
            text = _GRADE.sub(" ", text)
        conn = re.search(r"\b(and|or)\s*(?:an?\s*)?$", text.strip(), re.I)
        if conn:
            text = text.strip()[:conn.start()]
        kind = "co" if m[1].lower().startswith("co") else ("pc" if m[2] else "pre")
        out.append((kind, parse_expr(text), conn[1].lower() if conn else None))
    return out


def parse_requisites(text):
    pre = co = None
    concurrent = False
    for body in (m[1] for m in _BODY.finditer(text)):
        segs = _segments(body)
        if not segs:
            continue
        if len(segs) == 1:
            kind, e, _ = segs[0]
            if kind == "co":
                co = e
            else:
                pre, concurrent = e, concurrent or kind == "pc"
            continue
        groups = [[segs[0]]]                       # 'or' joins segments, 'and' starts a new group
        for prev, seg in zip(segs, segs[1:]):
            if prev[2] == "or":
                groups[-1].append(seg)
            else:
                groups.append([seg])
        pre_parts, co_parts = [], []
        for g in groups:
            items = [e for _, e, _ in g if e]
            if not items:
                continue
            e = items[0] if len(items) == 1 else ("or", items)
            # anything that may be taken at the same time as the course is checked like a corequisite
            (pre_parts if all(k == "pre" for k, _, _ in g) else co_parts).append(e)
        join = lambda parts: None if not parts else parts[0] if len(parts) == 1 else ("and", parts)
        pre = join(pre_parts) or pre
        co = join(co_parts) or co
    restr = [r.strip() for r in re.findall(r"Restricted to[^.]*\.", text)]
    return pre, co, concurrent, restr


def parse_subject(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for blk in soup.select("div.courseblock"):
        t = blk.select_one(".courseblocktitle")
        if not t:
            continue
        m = _TITLE.match(" ".join(t.get_text(" ").split()).replace("\u00a0", " "))
        if not m:
            continue
        code = f"{m[1]} {m[2]}"
        text = "\n".join(s.strip() for s in blk.get_text("\n").split("\n") if s.strip())
        req = re.search(r"Requisites?:\s*(.+?)(?=\n[A-Z][A-Za-z \-]{3,40}:|\Z)", text, re.S)
        pre, co, conc, restr = parse_requisites(" ".join(req[1].split()) if req else "")
        off = re.search(r"Typically Offered:?\s*([^\n]+)", text, re.I)
        offered = ({w for w in ("Fall", "Spring", "Summer") if w.lower() in off[1].lower()}
                   if off else None) or None
        only = re.search(r"Offered\s+((?:fall|spring|summer)(?:\s*(?:,|and|or|/)\s*(?:fall|spring|summer))*)\s+only", text, re.I)
        if only and not offered:
            offered = {w for w in ("Fall", "Spring", "Summer") if w.lower() in only[1].lower()}
        desc = blk.select_one(".courseblockdesc")
        dup = re.search(r"Duplicate Degree Credit Not Granted:(.*?)(?=Requisites?:|Additional Information:|Typically Offered|"
                        r"Restricted|\Z)", text.replace("\u00a0", " "), re.S)
        equiv = [f"{x[1]} {x[2]}" for x in CODE.finditer(dup[1])] if dup else []
        _TITLES[code] = m[4].strip()
        for other in equiv:
            _EQ_CATALOG.setdefault(code, set()).add(other)
            _EQ_CATALOG.setdefault(other, set()).add(code)
        dtext = desc.get_text(" ", strip=True) if desc else ""
        comp = tuple(dict.fromkeys(
            f"{x[1]} {x[2]}" for sent in re.findall(r"[^.]*(?:normally|should be|must be)[^.]*taken (?:concurrently )?with[^.]*\.", dtext, re.I)
            for x in CODE.finditer(sent) if f"{x[1]} {x[2]}" != code))
        out[code] = Course(code, m[4].strip(), float(m[3]), dtext, pre, co, conc, restr, offered, equiv, comp)
    return out


_WARNED = set()
_SUBJECT_MEMO = {}   # parsed subject pages, shared by every Catalog() in this process


class Catalog:
    """Lazy per-subject loader: asking for 'APPM 1350' loads the whole APPM page once."""

    def __init__(self):
        self.courses, self.loaded, self.failed = {}, set(), set()

    def load_subject(self, subj):
        if subj in self.loaded:
            return
        self.loaded.add(subj)
        try:
            if subj not in _SUBJECT_MEMO:
                _SUBJECT_MEMO[subj] = parse_subject(get_html(f"{BASE}/courses-a-z/{subj.lower()}/"))
            self.courses.update(_SUBJECT_MEMO[subj])
        except Exception as exc:                      # network / layout problem
            self.failed.add(subj)
            if subj not in _WARNED:                   # some subjects (HUMS, POLS) have no catalog page; say so once
                _WARNED.add(subj)
                print(f"  ! no catalog page for {subj} ({exc.__class__.__name__}); its courses are treated as unlisted")

    def get(self, code):
        self.load_subject(code.split()[0])
        return self.courses.get(code)

    def by_title(self, title):
        want = norm_title(title)
        for c in self.courses.values():
            if norm_title(c.title) == want:
                return c.code
        return None


# --------------------------------------------------------------------------
# Programs (degree / minor / certificate pages)
# --------------------------------------------------------------------------
@dataclass
class Slot:
    label: str
    options: list = field(default_factory=list)    # any-of; each option = bundle (list) of codes that are all required
    credits: float = 0
    children: list = field(default_factory=list)   # pool: choose `pick` of these slots
    pick: int = 0
    section: str = ""


@dataclass
class Program:
    title: str
    url: str
    slots: list
    plan: list                                     # [(label, [(item, credits)])] the catalog's own 4-year plan


def _hours(s):
    m = re.search(r"\d+(?:\.\d+)?", s or "")
    return float(m[0]) if m else 0.0


def _row_codes(tr):
    cell = tr.select_one("td.codecol")
    if cell:
        codes = [f"{m[1]} {m[2]}" for m in CODE.finditer(cell.get_text(" ").replace("\u00a0", " "))]
        if codes:
            return list(dict.fromkeys(codes))
    for scope in ([cell] if cell else []) + [tr]:
        codes = []
        for a in scope.select("a"):
            for src in (a.get("title", ""), a.get("onclick", ""), a.get("href", "")):
                c = norm_code(src)
                if c:
                    codes.append(c)
                    break
        if codes:
            return list(dict.fromkeys(codes))
    return []


def _elective_slots(text, hrs, section):
    """A requirement-table row without courses, e.g. 'Technical Electives 12' or the H&SS credit block."""
    raw = re.sub(r"\s*[\d.\-–]+\s*$", "", text)
    low = raw.lower()
    if "humanities" in low or "social science" in low:
        upper = re.search(r"at least (\d+) credits? must be upper", low)
        n = float(upper[1]) if upper else 0
        out = []
        if n:
            out.append(Slot("Upper-Division Humanities/Social Science Elective", credits=min(n, hrs), section=section))
        if hrs - n > 0:
            out.append(Slot("Humanities/Social Science Elective", credits=hrs - n, section=section))
        return out
    if "writing" in low:
        return [Slot("College-Approved Writing Course", credits=hrs, section=section)]
    return [Slot(re.sub(r"Electives\b", "Elective", raw).strip(), credits=hrs, section=section)]


CHOICE_HEADER = re.compile(
    r"\b(choose|select|one|two|three|four|any|at least)\b.*(following|\bthese\b|see below|:\s*[\d.\-–]*\s*$)",
    re.I)


def parse_program(html, url=""):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else url
    scope = soup.select_one("#requirementstextcontainer") or soup
    tables = scope.select("table.sc_courselist")[:1] or soup.select("table.sc_courselist")[:1]

    slots, section, pool, pool_group, pending = [], "", None, "", None
    focus_pool = focus = None
    for table in tables:
        for tr in table.select("tr"):
            cls = set(tr.get("class", []))
            for sup in tr.select("sup"):               # footnote markers would otherwise read as credit hours
                sup.decompose()
            text = tr.get_text(" ", strip=True).replace("\u00a0", " ")
            if not text or "listsum" in cls or text.lower().startswith("total"):
                continue
            if "areaheader" in cls or tr.select_one("span.areaheader"):
                if pending:                            # a credit block with no course list under it stays a generic slot
                    slots.append(pending)
                section, pool, pool_group, focus, focus_pool = text, None, "", None, None
                header_hours = _hours(re.search(r"[\d.]+\s*$", text)[0]) if re.search(r"\s[\d.]+\s*$", text) else 0
                pending = Slot(re.sub(r"\s*[\d.]+\s*$", "", text), credits=header_hours, section=section) if header_hours else None
                continue
            if "areasubheader" in cls:
                hours_cell = tr.select_one("td.hourscol")
                if hours_cell and _hours(hours_cell.get_text()) and re.search(r"focus", text, re.I):
                    if not focus_pool:                 # "choose one focus area" bundles of required courses
                        focus_pool = Slot("Focus area (choose one)", credits=_hours(hours_cell.get_text()), pick=1,
                                          section=section)
                        slots.append(focus_pool)
                    focus = Slot(re.sub(r"[:\s\d]+$", "", text), [[]], _hours(hours_cell.get_text()), section=section)
                    focus_pool.children.append(focus)
                    continue
                focus = None
            if "areasubheader" in cls and pool:
                pool_group = text.rstrip(": ")
                continue
            codes = _row_codes(tr)
            hours = tr.select_one("td.hourscol")
            hrs = _hours(hours.get_text(" ") if hours else "")
            is_or = "orclass" in cls or bool(re.match(r"or\b", text, re.I))
            comment = tr.select_one(".courselistcomment")
            selection = re.search(
                r"(?:select|choose|complete)\s+(?:(?:a\s+)?(?:maximum|minimum)\s+of\s+)?"
                r"(\w+)\s+(?:courses?\s+|(credits?|hours?)(?:\s+hours?)?,?\s+(?:chosen\s+|selected\s+)?)?(?:from|of)\b",
                text, re.I)
            if selection:
                word = selection[1].lower()
                pick = NUMWORDS.get(word) or (int(word) if word.isdigit() else 0)
                if selection[2] and pick:              # "Complete 9 credits, chosen from..." -> about 3 credits per course
                    pick = max(1, round(pick / 3))
                if pick:
                    base = slots[-1] if slots and not slots[-1].options and not slots[-1].children else None
                    pool = base or Slot(text, credits=hrs, section=section)
                    pool.pick = pick
                    if not base:
                        slots.append(pool)
                    pool_group = ""
                    continue
            if not codes and not is_or and focus is None and CHOICE_HEADER.search(text):
                count = next((NUMWORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in NUMWORDS), 0)
                if hrs or count:                       # "Choose one of the following:" etc. -> pick-N pool over the rows below
                    pool = Slot(text, credits=hrs, pick=count or max(1, round(hrs / 3)), section=section)
                    slots.append(pool)
                    pool_group = ""
                    continue
            if focus is not None and codes:
                focus.options[0].extend(c for c in codes if c not in focus.options[0])
                continue
            row_options = [[code] for code in codes] if len(codes) > 1 and "/" in text else [codes]
            if pending and codes:
                if not hrs:                            # "Electives 6" followed by zero-hour courses: choose from that list
                    pool = pending
                    pool.pick = max(1, round(pool.credits / 3))
                    slots.append(pool)
                pending = None
            if not pool and codes and not hrs and not is_or and slots and slots[-1].credits \
                    and not slots[-1].options and not slots[-1].children:
                pool = slots[-1]                       # zero-hour course rows under a credit requirement are its list
                pool.pick = pool.pick or max(1, round(pool.credits / 3))
            target = pool.children if pool else slots
            if codes and is_or and target:
                target[-1].options.extend(row_options)
            elif codes:
                target.append(Slot(pool_group or text, row_options, hrs, section=section))
                pool_group = ""
            elif comment and not hrs:
                continue
            elif hrs:
                pool = None
                slots.extend(_elective_slots(text, hrs, section))
            elif pool and not is_or:                       # title-only row inside a pick-N list
                pool.children.append(Slot(text, [[f"?{text}"]], section=section))

    if pending:
        slots.append(pending)
    flat = [x for x in slots if x.options and not x.credits]
    if len(flat) > 12 and len(flat) == len(slots):     # one undivided list: "a minimum of N credits" are required from it
        need = re.search(r"minimum of (\d+) credits", scope.get_text(" "))
        if need:
            slots = [Slot("Choose from the list", credits=float(need[1]), pick=max(1, round(int(need[1]) / 3)),
                          children=flat)]

    plan, year = [], ""
    for tr in soup.select("table.sc_plangrid tr"):
        cls = set(tr.get("class", []))
        text = tr.get_text(" ", strip=True)
        if "plangridyear" in cls:
            year = text
        elif "plangridterm" in cls:
            term = re.sub(r"\s*Credit Hours\s*$", "", text)
            plan.append((f"{year} - {term}", []))
        elif plan and not re.match(r"(credit hours|total)", text, re.I):
            codes = _row_codes(tr)
            cells = tr.select("td")
            names = [c.get_text(" ", strip=True) for c in cells if "hourscol" not in c.get("class", [])]
            item = codes[0] if codes else next((n for n in names if n), text)
            hrs = _hours(cells[-1].get_text(" ") if cells else "")
            plan[-1][1].append((item, hrs))
    return Program(title, url, slots, plan)


_PROGRAM_MEMO = {}


def load_program(url):
    if url not in _PROGRAM_MEMO:
        _PROGRAM_MEMO[url] = _load_program(url)
    return _PROGRAM_MEMO[url]


def _load_program(url):
    return parse_program(get_html(url), url)


_INDEX = {}


def _program_index():
    """Every degree/minor/certificate listed in the catalog's program sidebar: [(title, url, is_graduate)]."""
    if "rows" not in _INDEX:
        soup = BeautifulSoup(get_html(f"{BASE}/undergraduate/colleges-schools/arts-sciences/programs-study/",
                                      ttl=86400), "html.parser")
        rows, seen = [], set()
        for a in soup.select("a[href]"):
            href, title = urljoin(BASE, a["href"]), a.get_text(" ", strip=True).replace("\u200b", "")
            path = href.replace(BASE, "").strip("/").split("/")
            if len(path) >= 6 and path[0] in ("undergraduate", "graduate") and "programs-study" in path \
                    and path[-2] != "programs-study" and " - " in title and href not in seen:
                seen.add(href)
                rows.append((title, href, path[0] == "graduate"))
        _INDEX["rows"] = rows
    return _INDEX["rows"]


_ALIASES = {"bs": "bachelor science", "ba": "bachelor arts", "ms": "master science", "phd": "doctor philosophy",
            "undergrad": "bachelor", "major": "bachelor"}
_STOP = {"in", "of", "and", "the", "a", "degree", "program", "for"}


def find_programs(query):
    """Search the catalog's program list; returns [(title, url)] best match first."""
    words = []
    for w in re.findall(r"[a-z0-9]+", query.lower()):
        words += _ALIASES.get(w, w).split()
    words = [w for w in words if w not in _STOP]
    wants_grad = any(w in ("master", "doctor", "graduate", "phd") for w in words)
    scored = []
    try:
        rows = _program_index()
    except Exception:
        rows = []
    for title, url, grad in rows:
        if grad and not wants_grad:
            continue
        hay = re.findall(r"[a-z0-9]+", title.lower())
        hits = sum(any(h.startswith(w[:7]) for h in hay) for w in words)  # crude stem: astrophysics ~ astrophysical
        if words and hits == len(words):
            scored.append((-hits, len(hay), title, url))
    if scored or rows:
        return [(t, u) for _, _, t, u in sorted(scored)]
    html = get_html(f"{BASE}/search/?search={quote_plus(query)}", ttl=3600)
    soup, seen = BeautifulSoup(html, "html.parser"), {}
    for a in soup.select("a[href]"):
        href = urljoin(BASE, a["href"])
        path = href.replace(BASE, "").strip("/").split("/")
        if len(path) >= 5 and path[0] in ("undergraduate", "graduate") and a.get_text(strip=True):
            seen.setdefault(href, a.get_text(" ", strip=True))
    return [(t, u) for u, t in seen.items()]
