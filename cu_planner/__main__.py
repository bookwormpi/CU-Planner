"""python -m cu_planner {plan,schedule,register,go} ..."""
import argparse
import re
import sys
from datetime import datetime

from . import classsearch as cs
from . import registration as reg
from .catalog import Catalog, find_programs, load_program, norm_code
from .planner import Planner, Prefs, booking_priority, build_schedules, render_schedule

DAYMAP = {"m": 0, "t": 1, "w": 2, "r": 3, "f": 4, "s": 5, "u": 6}


def codes(text):
    return [c for c in (norm_code(x) for x in re.split(r"[,;\n]", text or "")) if c]


def hhmm(s):
    h, _, m = s.partition(":")
    return int(h) * 60 + int(m or 0)


def pick_program(arg, pick):
    if arg.startswith("http"):
        return arg
    hits = find_programs(arg)
    if not hits:
        sys.exit("No programs found. Open the program page on catalog.colorado.edu and pass its URL to --program.")
    if len(hits) > 1 and not pick:
        print("Several matches; re-run with --pick N (or pass the URL):")
        for i, (t, u) in enumerate(hits[:15], 1):
            print(f"  {i:2}. {t}\n      {u}")
        sys.exit(2)
    return hits[(pick or 1) - 1][1]


def do_plan(a):
    url = pick_program(a.program, a.pick)
    prog, cat = load_program(url), Catalog()
    print(f"\n{prog.title}\n{url}\n")
    if not prog.slots:
        sys.exit("Couldn't read a requirements table on that page (layout differs?). See README, 'If a scraper breaks'.")
    for s in prog.slots:                                 # warm catalog for subjects in the program
        for b in s.options + [o for ch in s.children for o in ch.options]:
            for c in b:
                if not c.startswith("?"):
                    cat.load_subject(c.split()[0])
    pl = Planner(cat, codes(a.completed), codes(a.prefer))
    required, generic = pl.expand(prog.slots)
    all_codes, reasons = pl.close_over_prereqs(required)
    start = cs.srcdb(a.start)
    offerings = cs.infer_offerings(set(all_codes), start) if a.infer_offerings else None
    terms, stuck = pl.sequence(all_codes, reasons, generic, start, a.max_credits, a.target_credits, offerings)
    for i, t in enumerate(terms, 1):
        print(f"{i}. {cs.term_name(t.db)}  ({t.credits:g} credits)")
        for name, cr, note in t.items:
            title = (cat.courses[name].title if name in cat.courses else "")
            print(f"     {name:10} {cr:>4g}  {title}{'  [' + note + ']' if note else ''}")
    if stuck:
        print("\nCould not place (prerequisite chain, term offering, or credit cap): " + ", ".join(stuck))
    extra = [c for c in all_codes if c in reasons]
    if extra:
        print("\nNot on the program table but required as prerequisites: " + ", ".join(extra))
    if prog.plan:
        print("\nThe catalog's own recommended plan, for comparison:")
        for label, items in prog.plan:
            print(f"  {label}: " + ", ".join(i for i, _ in items))
    print("\nNotes: standing/major restrictions, 'minimum grade C-' rules and capstone credit thresholds are\n"
          "not modeled; check each course's page and talk to your advisor before relying on the order.")
    return terms


def do_schedule(a, course_list=None, term=None):
    db = cs.srcdb(term or a.term)
    want = course_list or codes(a.courses)
    by_course = cs.term_sections(db, want)
    avoid = {DAYMAP[c] for c in (a.avoid_days or "").lower() if c in DAYMAP}
    prefs = Prefs(hhmm(a.earliest) if a.earliest else 0, hhmm(a.latest) if a.latest else 24 * 60, avoid,
                  a.include_closed, max_results=a.results)
    ranked, problems = build_schedules(by_course, prefs)
    print(f"\nSchedule options for {cs.term_name(db)}: {', '.join(want)}")
    for p in problems:
        print("  ! " + p)
    for i, (sc, chosen) in enumerate(ranked, 1):
        print(f"\n  Option {i}  (penalty {sc}; lower = tighter, later, fewer days)")
        print(render_schedule(chosen))
    if ranked:
        print("\n  Register in this order (fewest open sections first):")
        for opn, total, code in booking_priority(by_course):
            print(f"    {code}: {opn} open of {total} sections")
        print("\n  Caveat: recitation/lab pairing is inferred from section numbers; confirm the combo in Class Search before registering.")


def do_register(a, db=None):
    db = db or cs.srcdb(a.term)
    d = reg.get_dates(db, a.enroll_url)
    if not d:
        sys.exit(f"No built-in enrollment dates for {cs.term_name(db)}. Find the registrar's '"
                 f"{cs.term_name(db)} Continuing Student Enrollment Dates' post and pass it with --enroll-url.")
    print("\n" + reg.describe(d, a.credits))
    if a.ics:
        appt = datetime.strptime(a.appointment, "%Y-%m-%d %H:%M") if a.appointment else None
        print(f"\nWrote calendar file: {reg.write_ics(a.ics, d, a.credits, appt)}")


def main():
    ap = argparse.ArgumentParser(prog="cu_planner", description="CU Boulder degree, schedule and registration planner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common_plan(p):
        p.add_argument("--program", required=True, help="catalog URL, or search words like 'computer science bs'")
        p.add_argument("--pick", type=int, help="which search hit to use")
        p.add_argument("--completed", default="", help="courses already done/transferred, comma-separated")
        p.add_argument("--prefer", default="", help="options to favor when a requirement has alternatives")
        p.add_argument("--start", default="Spring 2027", help="first term to plan")
        p.add_argument("--max-credits", type=float, default=16)
        p.add_argument("--target-credits", type=float, default=15)
        p.add_argument("--infer-offerings", action="store_true", help="use past terms in Class Search to learn Fall/Spring-only courses")

    def common_sched(p):
        p.add_argument("--earliest", help="no classes before, e.g. 9:00")
        p.add_argument("--latest", help="no classes after, e.g. 17:00")
        p.add_argument("--avoid-days", help="letters: M T W R F (R = Thursday)")
        p.add_argument("--include-closed", action="store_true")
        p.add_argument("--results", type=int, default=5)

    def common_reg(p):
        p.add_argument("--enroll-url", help="registrar 'Continuing Student Enrollment Dates' post")
        p.add_argument("--ics", help="write a calendar file here")
        p.add_argument("--appointment", help="your exact slot from Buff Portal, 'YYYY-MM-DD HH:MM'")

    p = sub.add_parser("plan", help="term-by-term plan from a program page")
    common_plan(p)
    p = sub.add_parser("schedule", help="conflict-free section combinations for given courses")
    p.add_argument("--term", required=True)
    p.add_argument("--courses", required=True)
    common_sched(p)
    p = sub.add_parser("register", help="when your enrollment window opens")
    p.add_argument("--term", required=True)
    p.add_argument("--credits", type=float, required=True, help="completed credit hours (incl. transfer/AP)")
    common_reg(p)
    p = sub.add_parser("gui", help="open the graphical interface in your browser")
    p.add_argument("--port", type=int, default=8765)
    p = sub.add_parser("go", help="plan -> schedules for the first term -> registration dates")
    common_plan(p)
    common_sched(p)
    common_reg(p)
    p.add_argument("--credits", type=float, required=True)

    a = ap.parse_args()
    if a.cmd == "gui":
        from .gui import serve
        serve(a.port)
    elif a.cmd == "plan":
        do_plan(a)
    elif a.cmd == "schedule":
        do_schedule(a)
    elif a.cmd == "register":
        do_register(a)
    else:
        terms = do_plan(a)
        first = [n for n, _, _ in terms[0].items if norm_code(n)] if terms else []
        if first:
            do_schedule(a, first, a.start)
        do_register(a, cs.srcdb(a.start))


if __name__ == "__main__":
    main()
