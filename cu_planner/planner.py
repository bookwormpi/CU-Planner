"""Turns a Program + Catalog into (a) a term-by-term sequence and (b) schedule options."""
import heapq
import re
import math
from collections import defaultdict
from dataclasses import dataclass, field

from .catalog import and_groups, expr_codes, satisfied, with_equivalents
from .classsearch import DAYS, fmt_time


def next_terms(db, n):
    """n consecutive Fall/Spring term codes starting at db (a summer start rolls to Fall)."""
    out, yy, d = [], int(db[1:3]), int(db[3])
    d = 7 if d == 4 else d
    for _ in range(n):
        out.append(f"2{yy:02d}{d}")
        d, yy = (1, yy + 1) if d == 7 else (7, yy)
    return out


# --------------------------------------------------------------------------
# 1. Degree sequencing
# --------------------------------------------------------------------------
@dataclass
class PlannedTerm:
    db: str
    items: list = field(default_factory=list)     # [(code_or_label, credits, note)]

    @property
    def credits(self):
        return sum(c for _, c, _ in self.items)


class Planner:
    def __init__(self, catalog, completed=(), prefer=()):
        self.cat, self.done, self.prefer = catalog, with_equivalents(completed), set(prefer)
        self._depth, self._avail = {}, {}
        self.unverified = {}                           # course -> prerequisite groups the catalog can't satisfy

    # how many terms of prerequisites stand between you and this course
    def depth(self, code, _seen=frozenset()):
        if code in self.done:
            return 0
        if code in self._depth:
            return self._depth[code]
        c = self.cat.get(code)
        if not c or code in _seen:
            return 1
        d = 1 + self._edepth(c.prereq, _seen | {code})
        self._depth[code] = d
        return d

    def _edepth(self, e, seen):
        if not e:
            return 0
        if e[0] == "c":
            return self.depth(e[1], seen)
        parts = [self._edepth(s, seen) for s in e[1]]
        return max(parts) if e[0] == "and" else min(parts)

    def available(self, code, _seen=frozenset()):
        """Can this course actually be taken? It needs a catalog entry and a satisfiable prerequisite chain."""
        if code in self.done:
            return True
        if code in self._avail:
            return self._avail[code]
        course = self.cat.get(code)
        if not course:
            return False
        if code in _seen:
            return True
        ok = all(any(self.available(a, _seen | {code}) for a in expr_codes(g)) for g in and_groups(course.prereq))
        self._avail[code] = ok
        return ok

    def groups(self, expr, code=None):
        """Requirement groups that can be met; unmet ones the catalog can't satisfy are remembered, not enforced."""
        out = []
        for g in and_groups(expr):
            if any(self.available(a) for a in expr_codes(g)):
                out.append(g)
            elif code:
                self.unverified.setdefault(code, []).append(g)
        return out

    def _junk(self, code):
        """Placeholder courses (special topics, independent study) are a last resort, never a default pick."""
        c = self.cat.courses.get(code)
        return bool(c and re.match(r"(special topics|independent study|internship|thesis)", c.title, re.I))

    def _bundle(self, bundle):
        out = []
        for c in bundle:
            if c.startswith("?"):                      # title-only row in the catalog table
                c = self.cat.by_title(c[1:])
                if not c:
                    return None
            out.append(c)
        return out

    def choose(self, slot):
        """-> 'done' | list of codes | None (couldn't resolve)."""
        bundles = [b for b in (self._bundle(x) for x in slot.options) if b]
        if not bundles:
            return None
        for b in bundles:
            if all(c in self.done for c in b):
                return "done"
        return min(enumerate(bundles), key=lambda ib: (
            any(self._junk(c) for c in ib[1]), not (set(ib[1]) & self.prefer), sum(self.depth(c) for c in ib[1]), ib[0]))[1]

    def expand(self, slots):
        """Resolve every requirement into concrete courses and generic placeholders."""
        self.done = with_equivalents(self.done)        # subject pages loaded since __init__ may add equivalents
        for s in slots:                                # make sure title-only rows can resolve
            for ch in s.children:
                for b in ch.options:
                    for c in b:
                        if c.startswith("?"):
                            self.cat.load_subject(self._home_subject(slots))
        req, generic = [], []
        for s in slots:
            if s.children:
                picks = [(ch, self.choose(ch)) for ch in s.children]
                have = sum(1 for _, r in picks if r == "done")
                todo = [(ch, r) for ch, r in picks if isinstance(r, list)]
                todo.sort(key=lambda x: (any(self._junk(c) for c in x[1]), not (set(x[1]) & self.prefer), sum(self.depth(c) for c in x[1])))
                need = max(0, s.pick - have)
                for ch, r in todo[:need]:
                    req += [(c, s.label) for c in r]
                short = need - len(todo[:need])
                if short:
                    generic += [(f"{s.label.split(';')[0][:48]} (pick from list)", s.credits / max(s.pick, 1) or 3)] * short
            elif s.options:
                r = self.choose(s)
                if isinstance(r, list):
                    req += [(c, s.label) for c in r]
                elif r is None:
                    generic.append((s.label, s.credits or 3))
            elif s.credits:
                generic.append((s.label, s.credits))
        seen, out = set(), []
        for c, lbl in req:
            if c not in seen and c not in self.done and not (with_equivalents({c}) - {c}) & seen:
                seen.add(c)
                out.append((c, lbl))
        return out, generic

    @staticmethod
    def _home_subject(slots):
        counts = defaultdict(int)
        for s in slots:
            for b in s.options:
                for c in b:
                    if not c.startswith("?"):
                        counts[c.split()[0]] += 1
        return max(counts, key=counts.get) if counts else "CSCI"

    def close_over_prereqs(self, required):
        """Add prerequisites the program table doesn't list (e.g. a calculus or physics lead-in)."""
        plan, reasons, queue = dict.fromkeys(c for c, _ in required), {}, [c for c, _ in required]
        while queue:
            c = queue.pop(0)
            course = self.cat.get(c)
            if not course:
                continue
            for e in (course.prereq, course.coreq):
                for g in self.groups(e, c):
                    if satisfied(g, with_equivalents(self.done | set(plan))):
                        continue
                    alts = [a for a in expr_codes(g) if self.available(a)]
                    best = min(alts, key=lambda a: (self._junk(a), a not in self.prefer, self.depth(a), alts.index(a)))
                    plan[best] = None
                    reasons[best] = f"needed before {c}"
                    queue.append(best)
        return list(plan), reasons

    def sequence(self, required, reasons, generic, start_db, max_credits=16, target=15,
                 offerings=None, max_terms=14, rank=None, floors=None):
        floors = floors or {}
        rank = rank or {}                              # earliest term index per course (catalog order)
        cat, info = self.cat, {c: self.cat.get(c) for c in required}
        cr = lambda c: info[c].credits if info.get(c) else 3.0

        dependents = defaultdict(set)
        for c in required:
            if info[c]:
                for e in (info[c].prereq, info[c].coreq):
                    for p in expr_codes(e):
                        if p in info:
                            dependents[p].add(c)
        down = {}

        def downstream(c, seen=()):
            if c not in down:
                down[c] = 1 + max((downstream(d, seen + (c,)) for d in dependents[c] if d not in seen), default=0)
            return down[c]

        def ready(c, idx, placed):
            k = info[c]
            if not k:
                return True
            if idx < max(floors.get(c, 0), rank.get(c, 0)):   # never earlier than the catalog's recommended term
                return False
            before = with_equivalents(self.done | {p for p, i in placed.items() if i < idx})
            with_now = with_equivalents(self.done | {p for p, i in placed.items() if i <= idx})
            return (all(satisfied(g, with_now if k.concurrent_ok else before) for g in self.groups(k.prereq))
                    and all(satisfied(g, with_now) for g in self.groups(k.coreq)))

        chunks = []                                    # electives in <=3-credit pieces: (label, credits, earliest term)
        for g in generic:
            label, credits, earliest = (tuple(g) + (0,))[:3]
            n = max(1, math.ceil(credits / 3))
            chunks += [(label, round(credits / n, 1), earliest)] * n

        # Spread the remaining credits evenly over the terms the longest prerequisite chain allows,
        # instead of filling every early term to the cap and leaving a light tail.
        total = sum(cr(c) for c in required) + sum(c for _, c, _ in chunks)
        span = max([downstream(c) for c in required] + [max(r for r in rank.values()) + 1 if rank else 0]
                   + [max([e for _, _, e in chunks] or [0]) + 1, math.ceil(total / max(target, 1))])
        span = min(span, max_terms)

        # Courses missing from the catalog's plan (added prerequisites, unlisted alternates) inherit the term
        # just before their earliest listed dependent so they never block the recommended sequence.
        eff = {}

        def effective(c, seen=()):
            if c not in eff:
                if c in rank:
                    eff[c] = rank[c]
                else:
                    deps = [effective(d, seen + (c,)) for d in dependents[c] if d not in seen]
                    deps = [d for d in deps if d < 99]
                    eff[c] = max(0, min(deps) - 1) if deps else 99
            return eff[c]

        pair = defaultdict(set)                        # lecture <-> lab/recitation that must share a term
        for c in required:
            for o in (info[c].companion if info[c] and len(info[c].companion) == 1 else ()):
                if o in info:
                    pair[c].add(o)
                    pair[o].add(c)

        placed, terms, left = {}, [], total
        for idx, db in enumerate(next_terms(start_db, max_terms)):
            season = "Fall" if db[3] == "7" else "Spring"
            term, load = PlannedTerm(db), 0.0
            goal = min(max_credits, math.ceil(left / max(span - idx, 1))) if span > idx else max_credits
            cap = min(max_credits, goal + 1)

            def place_courses(listed):
                nonlocal load, left
                moved = True
                while moved and load < goal:
                    moved = False
                    todo = sorted((x for x in required if x not in placed and (effective(x) < 99) == listed),
                                  key=lambda x: (effective(x), -downstream(x), required.index(x)))
                    for c in todo:
                        offs = (offerings or {}).get(c) or (info[c].offered if info[c] else None)
                        if (offs and season not in offs) or load + cr(c) > cap or not ready(c, idx, placed):
                            continue
                        mates = [o for o in pair[c] if o not in placed]
                        if mates:                      # a lecture and its required lab/rec go together or not at all
                            trial = dict(placed)
                            trial[c] = idx
                            extra = sum(cr(o) for o in mates)
                            if load + cr(c) + extra > max_credits or not all(
                                    ready(o, idx, trial)
                                    and not (((offerings or {}).get(o) or (info[o].offered if info[o] else None)) or {season}).isdisjoint({season})
                                    for o in mates):
                                continue
                        placed[c] = idx
                        load += cr(c)
                        left -= cr(c)
                        unlocks = sorted(dependents[c])
                        note = "; ".join(filter(None, [
                            reasons.get(c, ""), f"unlocks {', '.join(unlocks[:4])}" if unlocks else ""]))
                        term.items.append((c, cr(c), note))
                        for o in mates:
                            placed[o] = idx
                            load += cr(o)
                            left -= cr(o)
                            term.items.append((o, cr(o), f"taken with {c}"))
                        moved = True
                        break

            def place_electives(only_now):
                nonlocal load, left
                candidates = sorted((k for k in chunks if (k[2] == idx if only_now else k[2] <= idx)),
                                    key=lambda k: (k[2], chunks.index(k)))
                for chunk in candidates:
                    if load + chunk[1] > cap:
                        continue
                    chunks.remove(chunk)
                    term.items.append((chunk[0], chunk[1], "choose a course"))
                    load += chunk[1]
                    left -= chunk[1]

            place_courses(True)          # courses the catalog plan schedules by this term
            place_courses(False)         # prerequisites and other unlisted courses take priority over electives
            place_electives(False)       # elective slots use capacity left after required courses
            if load < goal:
                for chunk in sorted(chunks, key=lambda k: (k[2], chunks.index(k))):
                    if load + chunk[1] <= cap:
                        chunks.remove(chunk)
                        term.items.append((chunk[0], chunk[1], "choose a course"))
                        load += chunk[1]
                        left -= chunk[1]
                    if load >= goal:
                        break
            if term.items:
                terms.append(term)
            if len(placed) == len(required) and not chunks:
                break
        while terms and not terms[-1].items:
            terms.pop()
        stuck = [c for c in required if c not in placed]
        return terms, stuck


# --------------------------------------------------------------------------
# 2. Schedule building
# --------------------------------------------------------------------------
@dataclass
class Prefs:
    earliest: int = 0
    latest: int = 24 * 60
    avoid_days: set = field(default_factory=set)
    include_closed: bool = False
    preferred_start: int = 9 * 60
    max_results: int = 5


def _fits(s, p):
    return all(a >= p.earliest and b <= p.latest and d not in p.avoid_days for d, a, b in s.meets)


def _clash(s, busy):
    return any(d == d2 and a < b2 and a2 < b for d, a, b in s.meets for d2, a2, b2 in busy)


def score(chosen, p):
    """Lower is better: minutes of gaps (>20 min) + 40 per campus day + minutes before preferred start."""
    by_day = defaultdict(list)
    for s in chosen:
        for d, a, b in s.meets:
            by_day[d].append((a, b))
    total = 40 * len(by_day)
    for iv in by_day.values():
        iv.sort()
        total += sum(g for g in (iv[i + 1][0] - iv[i][1] for i in range(len(iv) - 1)) if g > 20)
        total += max(0, p.preferred_start - iv[0][0])
    return total



def _enumerate(comps, p, limit, compatible=None):
    """Return the best conflict-free combinations found.

    compatible(chosen, candidate) may reject linked section combinations.
    """
    if any(not options for options in comps):
        return []

    compatible = compatible or (lambda chosen, candidate: True)
    order = sorted(range(len(comps)), key=lambda i: len(comps[i]))
    ordered = [comps[i] for i in order]
    out = []
    visited = 0

    def rec(i, chosen, busy):
        nonlocal visited
        if visited >= limit:
            return

        if i == len(ordered):
            visited += 1
            out.append((score(chosen, p), list(chosen)))
            return

        for section in ordered[i]:
            if _clash(section, busy):
                continue
            if not compatible(chosen, section):
                continue

            chosen.append(section)
            rec(i + 1, chosen, busy + section.meets)
            chosen.pop()

            if visited >= limit:
                break

    rec(0, [], [])
    return out


def _crn_linked(a, b):
    return str(b.crn) in a.linked or str(a.crn) in b.linked


def _linked(a, b, n):
    """Lecture/recitation/lab pairing by leading section digits (ASEN 1030: 030 <-> 031; PHYS: 100 <-> 101)."""
    if a.code != b.code or a.kind == b.kind or "LEC" not in (a.kind, b.kind):
        return True
    return a.no[:n] == b.no[:n]


def _link_rule(by_course):
    """Per course, use the longest section-number prefix (2 digits, then 1) under which every
    lecture and every recitation/lab has a partner; courses with no such prefix aren't constrained."""
    width, explicit = {}, set()
    for code, secs in by_course.items():
        if any(s.linked for s in secs):
            explicit.add(code)
            continue
        live = [s for s in secs if not s.cancelled and s.no.isdigit()]
        lecs = [s for s in live if s.kind == "LEC"]
        subs = [s for s in live if s.kind != "LEC"]
        if not (lecs and subs):
            continue
        for n in (2, 1):
            if (all(any(_linked(l, x, n) for x in subs) for l in lecs)
                    and all(any(_linked(l, x, n) for l in lecs) for x in subs)):
                width[code] = n
                break

    def compatible(chosen, cand):
        if cand.code in explicit:
            # a recitation and a lab each link to the lecture, not to each other
            return all(c.code != cand.code or c.kind == cand.kind
                       or (_crn_linked(c, cand) if "LEC" in (c.kind, cand.kind) else True)
                       or not (c.linked or cand.linked) for c in chosen)
        n = width.get(cand.code)
        return n is None or all(_linked(c, cand, n) for c in chosen)
    return compatible


def build_schedules(by_course, p, limit=100000, compatible=None, pins=()):
    """Build ranked schedules from {course_code: [Section]}.

    compatible(chosen, candidate) is an optional callback for enforcing
    lecture/lab/recitation pairing constraints. `pins` are CRNs the user chose: that section is used for its
    course/component even if it is closed or outside the time limits, and everything else is fitted around it.
    """
    problems, comps, labels = [], [], []
    pins = {str(c) for c in pins}

    for code, secs in by_course.items():
        live = [s for s in secs if not s.cancelled]

        if not live:
            problems.append(
                f"{code}: no sections found for this term "
                "(not offered, or the lookup failed)"
            )
            continue

        usable = [
            s for s in live
            if (p.include_closed or s.open) and _fits(s, p)
        ]

        pinned_here = any(s.crn in pins for s in live)
        if not p.include_closed and not pinned_here and not any(s.open for s in live):
            problems.append(
                f"{code}: every section is full/closed right now; "
                "run with --include-closed to plan around waitlists"
            )

        kinds = sorted({s.kind for s in live})
        for kind in kinds:
            opts = [s for s in live if s.kind == kind and s.crn in pins] or [s for s in usable if s.kind == kind]

            if not opts:
                problems.append(
                    f"{code}: no {kind} section is open and fits "
                    "your time/day limits"
                )

            comps.append(opts)
            labels.append(f"{code} {kind}")

    if not comps or any(not c for c in comps):
        return [], problems

    ranked = _enumerate(comps, p, limit, compatible or _link_rule(by_course))

    if not ranked:
        problems.append(
            "No conflict-free combination satisfies the current "
            "section, time, and pairing constraints."
            + (" Try clearing or changing a pinned section." if pins else "")
        )
        return [], problems

    ranked.sort(key=lambda x: x[0])

    # Keep one schedule per distinct weekly timetable: sections that meet at the
    # same times (duplicate records, or alternate CRNs) do not make a new option.
    unique = []
    seen = set()

    for value, sections in ranked:
        key = tuple(sorted(
            (s.code, s.kind, tuple(sorted(tuple(m) for m in s.meets)))
            for s in sections
        ))
        if key not in seen:
            seen.add(key)
            unique.append((value, sections))

        if len(unique) >= p.max_results:
            break

    return unique, problems



def fit_course(sections, p, busy):
    """Best conflict-free section combination for one course around already-booked meetings, or None."""
    ranked, _ = build_schedules({sections[0].code: sections}, p, limit=2000,
                                compatible=lambda chosen, cand: not _clash(cand, busy) and
                                all(_crn_linked(c, cand) or not (c.linked or cand.linked) for c in chosen))
    return ranked[0][1] if ranked else None


def render_schedule(chosen):
    by_day = defaultdict(list)
    for s in chosen:
        for d, a, b in s.meets:
            by_day[d].append((a, b, s))
    lines = [f"    {DAYS[d]}: " + "  |  ".join(f"{fmt_time(a)}-{fmt_time(b)} {s.code} {s.kind} {s.no}"
                                              for a, b, s in sorted(by_day[d], key=lambda x: x[0]))
             for d in sorted(by_day)]
    lines += [f"    (no set meeting time) {s.code} {s.kind} {s.no}" for s in chosen if not s.meets]
    lines.append("    CRNs: " + ", ".join(f"{s.code} {s.kind}={s.crn}" for s in chosen))
    return "\n".join(lines)


def booking_priority(by_course):
    """Courses with the fewest open sections should be registered first."""
    rows = []
    for code, secs in by_course.items():
        live = [s for s in secs if not s.cancelled]
        rows.append((sum(1 for s in live if s.open), len(live), code))
    return sorted(rows)
