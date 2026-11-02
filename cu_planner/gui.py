"""Local GUI: `python -m cu_planner gui` opens http://127.0.0.1:8765 in your browser.

The browser can't scrape CU's sites itself (cross-origin rules), so this tiny stdlib server does the
fetching and the page talks to it. Nothing leaves your machine except requests to colorado.edu.
"""
import json
import os
import secrets
import math
import re
import tempfile
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import classsearch as cs
from . import official
from . import registration as reg
from .catalog import (Catalog, and_groups, expr_codes, find_programs, load_program, norm_code, satisfied,
                      with_equivalents)
from .planner import PlannedTerm, Planner, Prefs, booking_priority, build_schedules, fit_course, next_terms
from .util import set_cookie

INDEX = Path(__file__).with_name("static") / "index.html"
DAYMAP = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5}


def _codes(text):
    return [c for c in (norm_code(x) for x in str(text or "").replace("\n", ",").replace(";", ",").split(",")) if c]


def api_programs(b):
    return {"choices": [{"title": t, "url": u} for t, u in find_programs(b["q"])[:150]]}


def _merge_program_requirements(plans, in_progress=()):
    required_by_course, programs_by_course, generic = {}, {}, []
    for program, required, electives in plans:
        generic.extend(electives)
        for code, label in required:
            required_by_course.setdefault(code, [])
            if label not in required_by_course[code]:
                required_by_course[code].append(label)
            programs_by_course.setdefault(code, set()).add(program.title)
        eligible = {
            code
            for slot in program.slots
            for option in slot.options + [option for child in slot.children for option in child.options]
            for code in option
            if not code.startswith("?")
        }
        for code in in_progress:
            if code in eligible:
                programs_by_course.setdefault(code, set()).add(program.title)
    required = [(code, "; ".join(labels)) for code, labels in required_by_course.items()]
    overlap = {code: sorted(programs) for code, programs in programs_by_course.items() if len(programs) > 1}
    return required, generic, programs_by_course, overlap


def _in_progress_term(db, codes, catalog, credit_info=None):
    term = PlannedTerm(db)
    for code in dict.fromkeys(codes):
        course = catalog.get(code)
        known = (credit_info or {}).get(code, {}).get("credits")
        term.items.append((code, known or (course.credits if course else 0.0), "in progress"))
    return term


def _next_full_term(db):
    return next_terms(db, 1)[0] if db[3] == "4" else next_terms(db, 2)[1]


def _requirements(course, done, planner=None):
    """Unmet prerequisite/corequisite alternatives for one course, so the page can re-check manual moves."""
    if not course:
        return {"prereq": [], "coreq": [], "offered": [], "concurrent": False}
    usable = planner.groups if planner else and_groups
    groups = lambda e: [expr_codes(g) for g in usable(e) if not any(c in done for c in expr_codes(g))]
    return {"prereq": groups(course.prereq), "coreq": groups(course.coreq),
            "offered": sorted(course.offered or []), "concurrent": bool(course.concurrent_ok)}


def _plan_offset(program, done, start_db):
    """How many catalog-plan terms are already covered by `done` (kept Fall/Spring-aligned with the start term)."""
    codes = [[norm_code(c) or c for item, _ in items for c in re.findall(r"[A-Z]{2,4}\s?\d{4}", item)]
             for _, items in program.plan]
    offset = 0
    while offset < len(codes) and codes[offset] and all(c in done for c in codes[offset]):
        offset += 1
    if (offset % 2 == 0) != (start_db[3] == "7"):
        offset += 1
    return codes, offset


def _recommended_rank(programs, done, start_db):
    """Map course -> earliest planning-term index implied by each program's catalog four-year plan."""
    rank = {}
    for program in programs:
        codes, offset = _plan_offset(program, done, start_db)
        for i, term in enumerate(codes):
            for code in term:
                rank[code] = min(rank.get(code, 99), max(0, i - offset))
    return rank


ELECTIVE_KINDS = [  # (keyword in label, kind)
    ("upper-division humanities", "hssup"), ("aerospace engineering elective", "aero"), ("technical elective", "technical"),
    ("math and science", "mathsci"), ("writing", "writing"), ("humanities", "hss"),
    ("social science", "hss"), ("free elective", "free")]
KIND_RULES = {  # kind -> (subjects, minimum course level, note shown in the picker)
    "aero": (["ASEN"], 4000, "Any ASEN course at the 4000 level or above that isn't already required."),
    "technical": (["ASEN", "APPM", "MATH", "PHYS", "ASTR", "MCEN", "ECEN", "CVEN", "CHEN", "CSCI", "STAT", "AREN", "GEEN"],
                  3000, "Generally a math, engineering, or science course at the 3000 level or above."),
    "mathsci": (["APPM", "MATH", "STAT", "PHYS", "ASTR", "CHEM", "GEOL", "ATOC", "EBIO", "MCDB"], 2000,
                "Suggestions only; the approved list is in your degree audit."),
    "hss": (["ECON", "PSYC", "PHIL", "SOCY", "ANTH", "HIST", "POLS", "ENGL", "ARTH", "LING", "GEOG", "ETHN", "RLST", "COMM"],
            1000, "Suggestions only; confirm against the college's approved Humanities & Social Sciences list."),
    "hssup": (["ECON", "PSYC", "PHIL", "SOCY", "ANTH", "HIST", "POLS", "ENGL", "ARTH", "LING", "GEOG", "ETHN", "RLST", "COMM"],
              3000, "Upper-division (3000+) humanities or social science; confirm against the approved list."),
    "writing": (["WRTG"], 1000, "Confirm against the college's approved writing courses."),
    "free": (["ASEN", "APPM", "MATH", "PHYS", "ASTR", "CSCI", "ECON", "PSYC", "PHIL", "WRTG", "ENVD", "ARTS"], 1000,
             "Any course you like."),
}
_ELECTIVE_CATALOG = Catalog()


def _approved(text):
    """'ASEN 4000-4999; CSCI 2270,2275,3000-4999' -> {subject: [(lo, hi), ...]} (lists copied from the BSAE degree audit)."""
    out = {}
    for part in text.split(";"):
        subject, _, nums = part.strip().partition(" ")
        for n in nums.split(","):
            lo, _, hi = n.strip().partition("-")
            out.setdefault(subject, []).append((int(lo), int(hi or lo)))
    return out


APPROVED = {  # kind -> explicit approved courses; kinds not listed here fall back to the subject/level rules in KIND_RULES
    "writing": _approved("ENES 1010,2000,3100; ENLP 3100; MCEN 3208; PHYS 3050; WRTG 3010,3030,3035,3070"),
    "mathsci": _approved(
        "APPM 3010,3170,3310,3350,3570,4380; ASTR 1030,2020,2030,2040,2100,3300,3710,3720,3750; "
        "ATOC 2050,3050,3070,3180,3300,3500,3600,3720,4200,4550,4700,4710,4720,4730,4750,4760,4770; "
        "CHEM 1133,3151; MATH 2001,2002,2510,3130,3450; PHYS 2130,2170; STAT 3100,4000"),
    "technical": _approved(
        "AIRR 3010; APPM 3000-4999; AREN 3000-4999; ASEN 4000-4999; ASTR 3000-4999; ATLS 3000-4999; ATOC 3000-4999; "
        "BMEN 3000-4999; BUSM 3020,3021; CHEM 3000-4999; CHEN 3000-4999; CSCI 2270,2275,2400,2824,3000-4999; "
        "CVEN 3000-4999; CYBR 3000-4999; EBIO 3000-4999; ECEN 2250,2260,2270,2350,2370,2410,2420,2703,3000-4999; "
        "EMEN 3000-4999; EVEN 3000-4999; GEEN 3400,3830; ERTH 3000-4999; IPHY 3000-4999; MATH 3000-4999; "
        "MCDB 3000-4999; MCEN 3000-4999; MILR 3052,3062; NAVR 3030,3040; PHYS 2130,2150,2170,2210,3000-4999; "
        "STAT 3000-4999; GEOG 3023,3053,3601,4023,4043,4093,4103,4201,4203,4303,4321,4403,4463,4503,4563,4603; "
        "INFO 3401,3402,4602,4604,4513,4652"),
    "aero": _approved("ASEN 4000-4999"),
}


def _official(kind):
    """Approved lists read from CU's own pages (see official.py); None when they couldn't be loaded."""
    if kind in ("technical", "mathsci"):
        return official.aerospace_lists().get(kind)
    codes = official.writing_courses() if kind == "writing" else official.hss_courses() if kind in ("hss", "hssup") else None
    if not codes:
        return None
    out = {}
    for c in codes:
        subject, number = c.split()
        out.setdefault(subject, []).append((int(number), int(number)))
    if kind in ("hss", "hssup"):
        for subject in official.ALL_PREFIXES:
            out[subject] = [(1000, 9999)]
    return out


def _approved_lists(kind):
    if kind not in _OFFICIAL_MEMO:
        _OFFICIAL_MEMO[kind] = _official(kind) or APPROVED.get(kind)
    return _OFFICIAL_MEMO[kind]


_OFFICIAL_MEMO = {}


def _allowed(kind, code):
    """Does this course satisfy an elective of this kind?"""
    subject, number = code.split()
    n = int(re.match(r"\d+", number)[0])
    lists = _approved_lists(kind) if kind != "aero" else APPROVED["aero"]
    if lists:
        if kind == "hssup" and n < 3000:
            return False
        return any(lo <= n <= hi for lo, hi in lists.get(subject, ()))
    if kind in APPROVED:
        return any(lo <= n <= hi for lo, hi in APPROVED[kind].get(subject, ()))
    subjects, level, _ = KIND_RULES[kind]
    return subject in subjects and n >= level


def _elective_kind(label):
    low = label.lower()
    return next((k for key, k in ELECTIVE_KINDS if key in low), "free")


def _course_text(e):
    if not e:
        return ""
    if e[0] == "c":
        return e[1]
    return (" or " if e[0] == "or" else " and ").join(x[1] if x[0] == "c" else f"({_course_text(x)})" for x in e[1])


def _course_info(c):
    return {"code": c.code, "title": c.title, "credits": c.credits, "desc": c.desc[:500],
            "prereq": _course_text(c.prereq), "coreq": _course_text(c.coreq),
            "offered": sorted(c.offered or []), "restrictions": c.restrictions[:2]}


def _double_credit_options(course_code, required):
    equivalents = with_equivalents({course_code})
    return [{"code": code, "programs": required[code]} for code in sorted(equivalents & required.keys())]


AERO_EXTRA = ["ASEN 3405", "ASEN 3406", "ASEN 4401", "ASEN 4402"]   # focus-area courses the catalog lets count here


NOTES = {
    "writing": "College of Engineering approved writing courses (Class Search 'ENGR GenEd: Writing Requirement').",
    "hss": "College of Engineering approved H&SS courses (Class Search attribute plus the college's list).",
    "hssup": "Approved H&SS courses at the 3000 level or above.",
    "technical": "Approved technical electives from the Aerospace department's list.",
    "mathsci": "Approved math & science electives from the Aerospace department's list.",
    "aero": "Any ASEN course at the 4000 level or above that isn't already required.",
}


def _elective_options(kind, exclude, required):
    note = KIND_RULES[kind][2]
    lists = _approved_lists(kind) if kind != "aero" else APPROVED["aero"]
    for subject in lists or KIND_RULES[kind][0]:
        _ELECTIVE_CATALOG.load_subject(subject)
    if lists:
        note = NOTES.get(kind, "From CU Boulder's approved list.")
    out = []
    for c in _ELECTIVE_CATALOG.courses.values():
        extra = kind == "aero" and c.code in AERO_EXTRA
        if (extra or _allowed(kind, c.code)) and c.code not in exclude:
            info = _course_info(c)
            info["double_credit"] = _double_credit_options(c.code, required)
            out.append(info)
    return note, out


def _required_map(b):
    return {x.get("code"): x.get("programs", []) for x in b.get("required", []) if isinstance(x, dict) and x.get("code")}


def api_electives(b):
    kind = b.get("kind") if b.get("kind") in KIND_RULES else _elective_kind(str(b.get("kind") or ""))
    note, out = _elective_options(kind, set(_codes(b.get("exclude"))), _required_map(b))
    return {"kind": kind, "note": note, "options": out[:2000]}


def api_connect(b):
    return {"connected": set_cookie(b.get("cookie"))}


def _meet_dicts(sections):
    return [{"code": s.code, "title": s.title, "kind": s.kind, "no": s.no, "crn": s.crn, "open": s.open,
             "status": s.status, "instructor": s.instructor, "meets": s.meets, "when": s.when()} for s in sections]


def _prefs(b):
    return Prefs(_mins(b.get("earliest"), 0), _mins(b.get("latest"), 24 * 60),
                 {DAYMAP[c] for c in (b.get("avoid_days") or "") if c in DAYMAP},
                 bool(b.get("include_closed")), max_results=int(b.get("results") or 200))


def _blocked_sections(by_course, prefs, pins):
    """CRNs that, if pinned (replacing any pin on the same course/component), leave no conflict-free schedule."""
    live = {code: [x for x in secs if not x.cancelled] for code, secs in by_course.items()}
    by_crn = {x.crn: (code, x.kind) for code, secs in live.items() for x in secs}
    blocked = []
    for code, secs in live.items():
        for kind in {x.kind for x in secs}:
            same = [x for x in secs if x.kind == kind]
            if len(same) < 2:
                continue
            base = [c for c in pins if by_crn.get(c) != (code, kind)]
            for x in same:
                ranked, _ = build_schedules(by_course, prefs, limit=1, pins=base + [x.crn])
                if not ranked:
                    blocked.append(x.crn)
    return blocked


def api_semester(b):
    """One term, end to end: timetable for the chosen courses, plus advisor-style suggestions for open slots."""
    db = cs.srcdb(b["term"])
    season = {"7": "Fall", "1": "Spring", "4": "Summer"}.get(db[3], "")
    courses = _codes(b.get("courses"))
    done = set(_codes(b.get("done")))
    prefs = _prefs(b)
    required = _required_map(b)
    out = {"term": cs.term_name(db), "problems": [], "options": [], "booking": [], "suggestions": {},
           "alternatives": {}, "live": True, "credits": 0}
    by_course, chosen = {}, []
    try:
        if courses:
            by_course = cs.term_sections(db, courses)
            known = {x.crn for secs in by_course.values() for x in secs if not x.cancelled}
            pins = [str(c) for c in (b.get("pins") or []) if str(c) in known]
            ranked, problems = build_schedules(by_course, prefs, pins=pins)
            out["problems"] += problems
            out["pins"] = pins
            out["sections"] = {code: _meet_dicts(sorted((x for x in secs if not x.cancelled), key=lambda x: (x.kind, x.no)))
                               for code, secs in by_course.items()}
            out["blocked"] = _blocked_sections(by_course, prefs, pins)
            out["options"] = [{"score": sc, "sections": _meet_dicts(c)} for sc, c in ranked]
            out["booking"] = [{"code": c, "open": o, "total": t} for o, t, c in booking_priority(by_course)]
            chosen = ranked[0][1] if ranked else []
    except cs.ClassSearchUnavailable as exc:
        out["live"] = False
        out["problems"].append(str(exc))
    busy = [m for s in chosen for m in s.meets]

    cat = Catalog()
    pl = Planner(cat, done | set(courses))
    pool = with_equivalents(done)
    sections_by_code = {}

    def sections_for(subject):
        if subject not in sections_by_code:
            sections_by_code[subject] = {}
            if out["live"]:
                try:
                    for s in cs.subject_sections(db, subject):
                        sections_by_code[subject].setdefault(s.code, []).append(s)
                except cs.ClassSearchUnavailable as exc:
                    out["live"] = False
                    out["problems"].append(str(exc))
        return sections_by_code[subject]

    def evaluate(info):
        """Is this course takeable now, and does it fit the timetable? -> suggestion dict or None."""
        course = cat.get(info["code"]) or _ELECTIVE_CATALOG.courses.get(info["code"])
        if not course or pl._junk(course.code):
            return None
        if any(not satisfied(g, pool) for g in pl.groups(course.prereq)):
            return None
        if any(not satisfied(g, pool | set(courses)) for g in pl.groups(course.coreq)):
            return None
        if course.offered and season and season not in course.offered:
            return None
        entry = dict(info)
        subject_rows = sections_for(course.code.split()[0]) if out["live"] else {}
        secs = subject_rows.get(course.code)
        if out["live"] and subject_rows:
            live = [s for s in (secs or []) if not s.cancelled]
            if not live:
                return None
            fit = fit_course(live, prefs, busy)
            if not fit:
                return None
            entry["sections"] = _meet_dicts(fit)
            entry["open"] = all(s.open for s in fit)
        return entry

    subjects_in_plan = {c.split()[0] for c in required} | {c.split()[0] for c in courses}
    for slot in b.get("slots") or []:
        kind = slot.get("kind")
        if kind not in KIND_RULES or kind in out["suggestions"]:
            continue
        note, options = _elective_options(kind, set(courses) | set(required), required)
        options = [o for o in options if o["code"] not in done and o["code"] not in courses]
        options.sort(key=lambda o: (not o["double_credit"], o["code"].split()[0] not in subjects_in_plan, o["code"]))
        found = []
        for info in options:
            entry = evaluate(info)
            if entry:
                entry["why"] = [w for w in (
                    f"also satisfies {', '.join(d['code'] for d in info['double_credit'])}" if info["double_credit"] else "",
                    "same department as your plan" if info["code"].split()[0] in subjects_in_plan else "",
                    "fits around your timetable" if "sections" in entry else "",
                    "open seats" if entry.get("open") else ("full right now" if "sections" in entry else ""),
                ) if w]
                found.append(entry)
            if len(found) >= 14:
                break
        out["suggestions"][kind] = {"note": note, "options": found}

    alts = b.get("alternatives") or {}
    for code in courses:
        if any(s.code == code for s in chosen) or not alts.get(code):
            continue
        found = []
        for alt in _codes(",".join(alts[code])):
            course = cat.get(alt)
            if course:
                entry = evaluate(_course_info(course))
                if entry:
                    entry["why"] = [f"alternative for {code}"]
                    found.append(entry)
        if found:
            out["alternatives"][code] = found
    out["credits"] = sum(cat.courses[c].credits for c in courses if c in cat.courses)
    return out


PLACEMENT = ["APPM 1235", "MATH 1011", "MATH 1021", "MATH 1150", "MATH 1160"]   # pre-calculus entry points


def _grid_timing(programs, done, start_db):
    """Terms (relative to the start) where each program's catalog plan puts each kind of elective."""
    when = {}
    for program in programs:
        _, offset = _plan_offset(program, done, start_db)
        for i, (_, items) in enumerate(program.plan):
            if i < offset:
                continue
            for item, hours in items:
                if hours and not re.search(r"[A-Z]{2,4}\s?\d{4}", item) and re.search(r"lective|Writing", item):
                    kind = _elective_kind(item)
                    when.setdefault("hss" if kind == "hssup" else kind, []).extend(
                        [max(0, i - offset)] * math.ceil(hours / 3))
    return {k: sorted(v) for k, v in when.items()}


def _elective_chunks(generic, timing):
    """Split elective requirements into <=3-credit slots, each with the earliest term the catalog plan uses."""
    used, out = {}, []
    for g in generic:
        label, credits = g[0], g[1]
        kind = _elective_kind(label)
        slots = timing.get("hss" if kind == "hssup" else kind, [])
        n = max(1, math.ceil(credits / 3))
        for _ in range(n):
            i = used.get(kind, 0)
            used[kind] = i + 1
            if kind == "hssup":                      # upper-division work belongs at the late end of the humanities slots
                pos = len(slots) - 1 - i
                earliest = slots[pos] if 0 <= pos < len(slots) else (slots[-1] if slots else 0)
            else:
                earliest = slots[i] if i < len(slots) else (slots[-1] if slots else 0)
            out.append((label, round(credits / n, 1), earliest))
    return out


def _absorb_minor_courses(generic, required, programs_by_course, major_title, cat):
    """Minor/second-program courses fill elective slots they qualify for (technical, then free, then math/science)
    instead of adding credits on top of the degree. -> (remaining elective chunks, {course: elective label})."""
    counts, generic = {}, list(generic)
    for code, _ in required:
        if major_title in programs_by_course.get(code, ()) or code not in cat.courses:
            continue
        course = cat.courses[code]
        subject, number = code.split()
        for kind in ("technical", "free", "mathsci"):
            if not _allowed(kind, code):
                continue
            picks = sorted((g for g in generic if _elective_kind(g[0]) == kind), key=lambda g: -g[2])
            covered, taken = 0.0, []
            for g in picks:
                if covered >= course.credits - 1.0:
                    break
                covered += g[1]
                taken.append(g)
            if taken and covered >= course.credits - 1.0:
                for g in taken:
                    generic.remove(g)
                counts[code] = taken[0][0]
                break
    return generic, counts


HSS_EXTRA = {"HUMS", "EHON", "ARSC", "ARTS", "CLAS", "FREN", "GRMN", "SPAN", "ITAL", "JPNS", "CHIN", "RUSS"}


def _slot_codes(slots):
    out = set()
    for slot in slots:
        out.update(c for opt in slot.options for c in opt if not c.startswith("?"))
        out |= _slot_codes(slot.children)
    return out


def _earned_kinds(code):
    subject, number = code.split()
    level = int(number)
    kinds = []
    for kind in ("hssup", "hss", "aero", "technical", "mathsci", "writing"):
        if _allowed(kind, code) and kind not in kinds:
            kinds.append(kind)
    return kinds + ["free"]


def _absorb_earned(generic, courses):
    """Courses already earned or in progress that aren't named requirements fill elective slots (the audit's own
    grouping when known, else by subject and level). -> (remaining chunks, [{code, credits, counts_as}])."""
    generic, used = list(generic), []
    for code, credits, kind in courses:
        kinds = [] if kind == "extra" else ([kind] if kind in KIND_RULES else _earned_kinds(code))
        if kind in KIND_RULES and kind != "free":
            kinds.append("free")                         # an audit group we have no slots left for still counts as free
        label = ""
        for k in kinds:
            picks = sorted((g for g in generic if _elective_kind(g[0]) == k), key=lambda g: -g[2])
            covered, taken = 0.0, []
            for g in picks:
                if covered >= credits - 1.0:
                    break
                covered += g[1]
                taken.append(g)
            if taken and covered >= credits - 1.0:
                for g in taken:
                    generic.remove(g)
                label = taken[0][0]
                break
        used.append({"code": code, "credits": credits, "counts_as": label})
    return generic, used


def api_audit(b):
    from .audit import parse_audit
    result = parse_audit(b.get("text"))
    if not result["courses"]:
        raise ValueError("No courses found. Open your degree audit, select the whole page, copy, and paste it here.")
    return result


def api_plan(b):
    requested = b.get("programs") or [b.get("program", "")]
    requested = [(index, str(value).strip()) for index, value in enumerate(requested) if str(value).strip()]
    if not requested:
        raise ValueError("Enter at least one catalog program.")
    urls = []
    for index, value in requested:
        if value.startswith("http"):
            urls.append(value)
            continue
        hits = find_programs(value)
        if not hits:
            raise ValueError(f"No programs matched {value!r}. Paste its catalog page URL instead.")
        if len(hits) > 1:
            return {"choices": [{"title": t, "url": u} for t, u in hits[:15]], "program_index": index}
        urls.append(hits[0][1])
    programs = [load_program(url) for url in urls]
    for program in programs:
        if not program.slots:
            raise ValueError(f"Couldn't read requirements for {program.title} (see README: 'If a scraper breaks').")
    cat = Catalog()
    for program in programs:
        for slot in program.slots:
            for opt in slot.options + [o for child in slot.children for o in child.options]:
                for code in opt:
                    if not code.startswith("?"):
                        cat.load_subject(code.split()[0])
    completed = _codes(b.get("completed"))
    in_progress = [code for code in _codes(b.get("in_progress")) if code not in completed]
    for code in in_progress:
        cat.load_subject(code.split()[0])
    for code in completed:
        cat.load_subject(code.split()[0])
    credit_info = {}
    for code, info in (b.get("credit_info") or {}).items():
        code = norm_code(code)
        if code and isinstance(info, dict):
            credit_info[code] = {"credits": float(info.get("credits") or 0), "kind": info.get("kind")}
    placed_out = [] if b.get("needs_precalc") else PLACEMENT      # assume calculus-ready placement by default
    pl = Planner(cat, completed + in_progress + placed_out, _codes(b.get("prefer")))
    focus_pref = str(b.get("focus") or "").strip().lower()
    if focus_pref:
        for program in programs:
            for slot in program.slots:
                for child in slot.children:
                    if focus_pref in child.label.lower():
                        pl.prefer.update(c for opt in child.options for c in opt)
    plans = []
    for program in programs:
        pl.prefer.update(norm_code(c) or c for _, items in program.plan
                         for item, _ in items for c in re.findall(r"[A-Z]{2,4}\s?\d{4}", item))
    for program in programs:
        required, electives = pl.expand(program.slots)
        plans.append((program, required, electives))
        pl.prefer.update(code for code, _ in required)
    required, generic, programs_by_course, overlap = _merge_program_requirements(plans, in_progress)
    allc, reasons = pl.close_over_prereqs(required)
    start = cs.srcdb(b.get("start") or "Spring 2027")
    off = cs.infer_offerings(set(allc), start) if b.get("infer") else None
    planning_start = _next_full_term(start) if in_progress else start
    rank = _recommended_rank(programs, pl.done, planning_start)
    generic = _elective_chunks(generic, _grid_timing(programs, pl.done, planning_start))
    counts_as = {}
    if len(programs) > 1:
        generic, counts_as = _absorb_minor_courses(generic, required, programs_by_course, programs[0].title, cat)
    explicit = with_equivalents(set().union(*(_slot_codes(program.slots) for program in programs)))
    earned_courses = []
    for code in dict.fromkeys(completed + in_progress):
        info = credit_info.get(code, {})
        kind = info.get("kind")
        if code in explicit and kind not in KIND_RULES:
            continue                                     # already satisfies a named requirement
        course = cat.courses.get(code)
        earned_courses.append((code, info.get("credits") or (course.credits if course else 3.0), kind))
    generic, earned_used = _absorb_earned(generic, earned_courses)
    for item in earned_used:
        if item["counts_as"]:
            counts_as[item["code"]] = item["counts_as"]
    floors = {}
    for c in allc:
        pl.groups(cat.courses[c].prereq if c in cat.courses else None, c)
    for c in pl.unverified:
        if c in allc and c in rank:
            floors[c] = rank[c]
    raw_years = b.get("finish_years")
    try:
        years = 4 if raw_years in (None, "") else int(raw_years)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a completion time between 1 and 10 years.") from exc
    if not 1 <= years <= 10:
        raise ValueError("Choose a completion time between 1 and 10 years.")
    max_terms = max(1, years * 2 - int(bool(in_progress)))
    elective_credits = sum(credits for _, credits, *_ in generic)
    caps = [float(b["max_credits"])] if b.get("max_credits") else [16.0, 17.0, 18.0]
    for cap in caps:                              # raise the per-term load only as far as the finish date needs
        terms, stuck = pl.sequence(allc, reasons, list(generic), planning_start, cap,
                                   float(b.get("target_credits") or min(cap, 15)), off, max_terms=max_terms,
                                   rank=rank, floors=floors)
        scheduled_elective_credits = sum(credits for term in terms for _, credits, note in term.items
                                         if note == "choose a course")
        if not stuck and scheduled_elective_credits >= elective_credits - 0.01:
            break
    if in_progress:
        terms.insert(0, _in_progress_term(start, in_progress, cat, credit_info))
    return {
        "title": " + ".join(program.title for program in programs), "url": urls[0],
        "programs": [{"title": program.title, "url": url} for program, url in zip(programs, urls)],
        "overlap": [{"code": code, "programs": names} for code, names in sorted(overlap.items())],
        "earned_credit": [{**item, "title": (cat.courses[item["code"]].title if item["code"] in cat.courses else ""),
                           "in_progress": item["code"] in in_progress} for item in earned_used],
        "stuck": stuck, "added": [c for c in allc if c in reasons], "max_load": max((t.credits for t in terms), default=0),
        "total_credits": round(sum(t.credits for t in terms), 1),
        "focus_areas": [child.label for program in programs for slot in program.slots
                        if "focus" in slot.label.lower() for child in slot.children],
        "unverified": [{"course": c, "needs": [expr_codes(g) for g in groups]} for c, groups in pl.unverified.items()
                       if c in allc],
        "finish_years": years, "term_limit": max_terms,
        "unplaced_elective_credits": round(max(0, elective_credits - scheduled_elective_credits), 1),
        "terms": [{"db": t.db, "name": cs.term_name(t.db), "credits": t.credits,
                   "current": bool(in_progress and t.db == start), "items": [
            {"name": n, "credits": c, "note": note, "title": cat.courses[n].title if n in cat.courses else "",
             "course": n in cat.courses or bool(norm_code(n)), "added": n in reasons, "counts_as": counts_as.get(n, ""),
             "kind": "" if (n in cat.courses or norm_code(n)) else _elective_kind(n),
             "programs": sorted(programs_by_course.get(n, ())), **_requirements(cat.courses.get(n), set(completed), pl)}
            for n, c, note in t.items]} for t in terms],
        "catalog_plan": [{"label": f"{program.title} — {label}", "items": [i for i, _ in items]}
                         for program in programs for label, items in program.plan],
    }


def api_schedule(b):
    db, want = cs.srcdb(b["term"]), _codes(b.get("courses"))
    if not want:
        raise ValueError("Enter at least one course code, such as CSCI 2270.")
    by_course = cs.term_sections(db, want)
    prefs = Prefs(_mins(b.get("earliest"), 0), _mins(b.get("latest"), 24 * 60),
                  {DAYMAP[c] for c in (b.get("avoid_days") or "") if c in DAYMAP},
                  bool(b.get("include_closed")), max_results=int(b.get("results") or 200))
    ranked, problems = build_schedules(by_course, prefs)
    return {
        "term": cs.term_name(db), "problems": problems,
        "options": [{"score": sc, "sections": [
            {"code": s.code, "title": s.title, "kind": s.kind, "no": s.no, "crn": s.crn,
             "open": s.open, "status": s.status, "instructor": s.instructor, "meets": s.meets,
             "when": s.when()} for s in chosen]}
            for sc, chosen in ranked],
        "booking": [{"code": c, "open": o, "total": t} for o, t, c in booking_priority(by_course)],
    }


def _mins(s, default):
    if not s:
        return default
    try:
        h, m = (int(part) for part in s.split(":"))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid time {s!r}; use HH:MM.") from None
    if not 0 <= h < 24 or not 0 <= m < 60:
        raise ValueError(f"Invalid time {s!r}; use a time from 00:00 to 23:59.")
    return h * 60 + m


def _iso(d):
    return d.isoformat() if d else None


def api_register(b):
    db = cs.srcdb(b["term"])
    d = reg.get_dates(db, b.get("enroll_url") or None)
    if not d:
        raise ValueError(f"No built-in enrollment dates for {cs.term_name(db)}. Paste the registrar's "
                         "'Continuing Student Enrollment Dates' post URL into the box and try again.")
    mine = reg.block_for(float(b["credits"]), d)
    return {
        "term": d.term or cs.term_name(db), "source": d.source, "extras": d.extras,
        "blocks": [{"label": x.label, "start": x.start.isoformat(), "credit_based": x.min_credits is not None,
                    "mine": x is mine} for x in sorted(d.blocks, key=lambda x: x.start)],
        "cart_opens": _iso(d.cart_opens), "open_enrollment": _iso(d.open_enrollment),
        "reservation": [_iso(d.reservation[0]), d.reservation[1].isoformat()] if d.reservation else None,
    }


STATE_DIR = Path(os.environ.get("CU_PLANNER_STATE_DIR") or Path.home() / ".config" / "cu_planner" / "users")
MAX_STATE_BYTES = 8 * 1024 * 1024
COOKIE = "cu_planner_id"
_state_lock = threading.Lock()
_ID_OK = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


def _state_path(uid):
    if not _ID_OK.match(uid or ""):
        raise ValueError("bad visitor id")
    return STATE_DIR / f"{uid}.json"


def load_state(uid):
    try:
        return json.loads(_state_path(uid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(uid, body):
    if not isinstance(body, dict):
        raise ValueError("state must be an object")
    data = json.dumps(body)
    if len(data) > MAX_STATE_BYTES:
        raise ValueError("saved state is too large")
    path = _state_path(uid)
    with _state_lock:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)                             # atomic, so a crash can't leave half a file
    return {"ok": True}


ROUTES = {"/api/programs": api_programs, "/api/plan": api_plan, "/api/electives": api_electives,
          "/api/schedule": api_schedule, "/api/register": api_register, "/api/semester": api_semester,
          "/api/connect": api_connect, "/api/audit": api_audit}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _visitor(self):
        """-> (id, Set-Cookie header or None). Each browser gets its own random id, so each person has their own saved page."""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and _ID_OK.match(value):
                return value, None
        uid = secrets.token_urlsafe(24)
        return uid, {"Set-Cookie": f"{COOKIE}={uid}; Path=/; Max-Age=315360000; HttpOnly; SameSite=Lax"}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif u.path == "/api/state":
            uid, cookie = self._visitor()
            self._send(200, json.dumps(load_state(uid)), extra={"Cache-Control": "no-store", **(cookie or {})})
        elif u.path == "/api/ics":
            try:
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                d = reg.get_dates(cs.srcdb(q["term"]), q.get("enroll_url") or None)
                appt = datetime.strptime(q["appointment"], "%Y-%m-%dT%H:%M") if q.get("appointment") else None
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "registration.ics"
                    reg.write_ics(path, d, float(q["credits"]), appt)
                    data = path.read_bytes()
                self._send(200, data, "text/calendar",
                           {"Content-Disposition": 'attachment; filename="registration.ics"'})
            except Exception as exc:
                self._send(400, str(exc), "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        fn, extra = ROUTES.get(path), None
        if path == "/api/state":
            uid, extra = self._visitor()
            fn = lambda b: save_state(uid, b)
        if not fn:
            return self._send(404, "{}")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self._send(200, json.dumps(fn(body)), extra=extra)
        except json.JSONDecodeError as exc:
            self._send(400, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        except cs.ClassSearchUnavailable as exc:
            self._send(503, json.dumps({"error": str(exc)}))
        except (ValueError, KeyError, TypeError) as exc:
            self._send(400, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        except Exception as exc:                       # surface any scraper/network problem in the UI
            self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))


def serve(port=8765, open_browser=True):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"CU Planner running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
