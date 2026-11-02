"""Read the text of a CU degree audit (copied from the Audit Results page) into earned / in-progress courses."""
import re

COURSE = re.compile(
    r"^\s*(FA|SP|SU)\s?(\d{2})\s*([A-Z]{2,4})\s?(\d{4})([A-Z]{0,2})\s+(\d+(?:\.\d+)?)\s*"
    r"(\*\*\*|T[A-Z][+-]?|[A-DF][+-]?|P\+?|S|NR|W|I)?\s*(.*)$")
EQUIV = re.compile(r"^\s*[A-Z]{2}-[A-Z]{2,4}:\s*([A-Z]{2,4})\s?(\d{4})")
NOT_EARNED = {"F", "W", "NR", "I"}

# audit heading -> the elective kind its courses fill; None means a named requirement
SECTIONS = [
    (re.compile(r"^College Writing Requirement"), "writing"),
    (re.compile(r"^Upper Division Humanities"), "hssup"),
    (re.compile(r"^Remaining Humanities"), "hss"),
    (re.compile(r"^Aerospace Engineering Elective \("), "aero"),
    (re.compile(r"^Technical Elective \("), "technical"),
    (re.compile(r"^Free Electives?\b"), "free"),
    (re.compile(r"^Other Coursework Not Applied"), "extra"),
    (re.compile(r"^(Math and Science \(|Basic Engineering Electives|Aerospace Engineering Sciences \()"), None),
    (re.compile(r"^(Coursework History|Transfer Credit)"), None),
]


def parse_audit(text):
    """-> {completed, in_progress, courses[{code,title,credits,term,status,transfer,kind}], earned_hours, in_progress_hours}"""
    courses, order = {}, []
    section, in_summary, last = None, False, None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Bachelor of Science Summary") or line.startswith("Bachelor of Arts Summary"):
            in_summary = True
        if in_summary:
            for pattern, kind in SECTIONS:
                if pattern.match(line):
                    section = kind
                    break
        m = EQUIV.match(line)
        if m and last:
            courses[last]["equivalent"] = f"{m[1]} {m[2]}"
            continue
        m = COURSE.match(line)
        if not m:
            continue
        season, yy, subject, number, suffix, credits, grade, title = m.groups()
        if grade in NOT_EARNED:
            last = None
            continue
        key = (season + yy, subject, number + suffix)
        entry = courses.get(key)
        if entry is None:
            entry = courses[key] = {
                "code": f"{subject} {number}", "title": title.strip(), "credits": float(credits),
                "term": season + yy, "status": "in_progress" if grade == "***" else "earned",
                "transfer": bool(grade and grade.startswith("T")), "kind": None, "equivalent": None}
            order.append(key)
        if section and not entry["kind"]:
            entry["kind"] = section
        last = key
    out = []
    for key in order:
        c = courses[key]
        if c["equivalent"]:
            c["code"] = c["equivalent"]
        c.pop("equivalent")
        out.append(c)
    merged = {}
    for c in out:                                    # the same course can be listed under several headings
        merged.setdefault(c["code"], c)
    out = list(merged.values())
    done = [c for c in out if c["status"] == "earned"]
    now = [c for c in out if c["status"] == "in_progress"]
    return {
        "courses": out,
        "completed": [c["code"] for c in done],
        "in_progress": [c["code"] for c in now],
        "earned_hours": sum(c["credits"] for c in done),
        "in_progress_hours": sum(c["credits"] for c in now),
    }
