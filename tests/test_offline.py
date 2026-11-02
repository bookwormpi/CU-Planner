"""Offline checks (no network). Run:  python tests/test_offline.py
If the live sites change layout, update the fixtures here first, then fix the parsers."""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cu_planner import classsearch as cs
from cu_planner import gui
from cu_planner import registration as reg
from cu_planner.catalog import Catalog, Course, Program, Slot, parse_expr, parse_program, parse_subject, satisfied
from cu_planner.planner import Planner, Prefs, build_schedules
from cu_planner import util

SUBJECT_HTML = """
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 1300 (4) Computer Science 1: Starting Computing</strong></p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 2270 (4) Computer Science 2: Data Structures</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Requires prerequisite courses of (CSCI 1300 or ECEN 1310) and (APPM 1350 or MATH 1300) (all minimum grade C-).</p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 2400 (4) Computer Systems</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Requires prerequisite course of CSCI 2270 (minimum grade C-).</p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 2824 (3) Discrete Structures</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Requires prerequisite courses of (CSCI 1300 or ECEN 1310) and (APPM 1345 or APPM 1350 or MATH 1300) (all minimum grade C-).</p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 3104 (4) Algorithms</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Requires prerequisite courses of CSCI 2270 and CSCI 2824 (all minimum grade C-).</p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 3434 (3) Theory of Computation</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Requires prerequisite course of CSCI 3104 (minimum grade C-).</p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 2834 (1) Discrete Structures Workgroup</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Requires corequisite course of CSCI 2824.</p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>CSCI 1000 (1) Computer Science as a Field of Work and Study</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Restricted to students with 0-26 credits (Freshmen) Computer Science (CSEN-BSCS or CSEN-BA) majors only.</p></div>
"""
APPM_HTML = """
<div class="courseblock"><p class="courseblocktitle"><strong>APPM 1350 (4) Calculus 1 for Engineers</strong></p></div>
<div class="courseblock"><p class="courseblocktitle"><strong>APPM 1360 (4) Calculus 2 for Engineers</strong></p>
<p class="courseblockextra"><strong>Requisites:</strong> Requires prerequisite course of APPM 1350 or MATH 1300 (minimum grade C-).</p></div>
"""
PROGRAM_HTML = """
<h1>Computer Science Bachelor of Science (BS)</h1>
<div id="requirementstextcontainer"><table class="sc_courselist"><tbody>
<tr class="areaheader"><td colspan="3"><span class="courselistcomment areaheader">Computer Science Foundation</span></td></tr>
<tr class="even"><td class="codecol"><a href="/search/?P=CSCI%201000" title="CSCI 1000" class="bubblelink code">CSCI&nbsp;1000</a></td><td>CS as a Field</td><td class="hourscol">1</td></tr>
<tr class="odd orclass"><td class="codecol"><span class="blockindent">or&nbsp;</span><a title="ASEN 1000">ASEN&nbsp;1000</a></td><td>Intro Aerospace</td><td class="hourscol"></td></tr>
<tr class="even"><td class="codecol"><a title="CSCI 1300">CSCI&nbsp;1300</a></td><td>CS 1</td><td class="hourscol">4</td></tr>
<tr class="odd"><td class="codecol"><a title="CSCI 2270">CSCI&nbsp;2270</a></td><td>CS 2</td><td class="hourscol">4</td></tr>
<tr class="even"><td class="codecol"><a title="CSCI 2400">CSCI&nbsp;2400</a></td><td>Systems</td><td class="hourscol">4</td></tr>
<tr class="odd"><td class="codecol"><a title="CSCI 2824">CSCI&nbsp;2824</a></td><td>Discrete</td><td class="hourscol">3</td></tr>
<tr class="even"><td class="codecol"><a title="CSCI 3104">CSCI&nbsp;3104</a></td><td>Algorithms</td><td class="hourscol">4</td></tr>
<tr class="odd"><td class="codecol"><a title="APPM 1360">APPM&nbsp;1360</a></td><td>Calc 2</td><td class="hourscol">4</td></tr>
<tr class="even"><td colspan="2">Computer Science Core</td><td class="hourscol">6-9</td></tr>
<tr class="odd"><td colspan="3"><span class="courselistcomment">Select two courses from approved list below</span></td></tr>
<tr><td class="codecol"></td><td>Theory of Computation</td><td class="hourscol"></td></tr>
<tr><td class="codecol"></td><td><a href="/search/?P=CSCI%202834">Discrete Structures Workgroup</a></td><td class="hourscol"></td></tr>
<tr class="even"><td colspan="2">Free Electives</td><td class="hourscol">6</td></tr>
<tr class="listsum"><td colspan="2">Total Credit Hours</td><td class="hourscol">45</td></tr>
</tbody></table></div>
<table class="sc_plangrid"><tbody>
<tr class="plangridyear"><td colspan="3">First Year</td></tr>
<tr class="plangridterm"><td colspan="2">Fall Semester</td><td>Credit Hours</td></tr>
<tr class="even"><td class="codecol"><a title="APPM 1350">APPM 1350</a></td><td>Calc 1</td><td class="hourscol">4</td></tr>
<tr class="even"><td class="codecol"></td><td>Natural Science Elective</td><td class="hourscol">3</td></tr>
<tr class="plangridsum"><td colspan="2">Credit Hours</td><td>7</td></tr>
</tbody></table>
"""
ENROLL_HTML = """<p><strong>Monday, Nov. 2, 2026</strong></p><ul>
<li>8:30 a.m. – Priority registration students**</li><li>9:30 a.m. – Seniors (90+ credits)*</li></ul>
<p><strong>Monday, Nov. 9, 2026</strong></p><ul><li>8:30 a.m. – Juniors (60–89.99 credits)*</li></ul>
<p><strong>Monday, Nov. 16, 2026</strong></p><ul><li>8:30 a.m. – Freshmen (0–7.99 credits)</li><li>9:30 a.m. – Freshmen (8–29.99 credits)*</li></ul>
<p>Registration remains open through open enrollment, which begins <strong>Monday, Jan. 4, 2027</strong>.</p>"""


def test_expr():
    e = parse_expr("CSCI 1300 or CSCI 1310 or ECEN 1310 and APPM 1345 or APPM 1350 or MATH 1300")
    assert e[0] == "and" and [x[0] for x in e[1]] == ["or", "or"], e
    assert satisfied(e, {"ECEN 1310", "MATH 1300"}) and not satisfied(e, {"CSCI 1300"})
    assert parse_expr("CSCI 2270 and 2824") == ("and", [("c", "CSCI 2270"), ("c", "CSCI 2824")])
    assert parse_expr("CSCI 2270 or instructor consent") == ("c", "CSCI 2270")
    assert parse_expr("") is None


def make_catalog():
    cat = Catalog()
    cat.courses.update(parse_subject(SUBJECT_HTML))
    cat.courses.update(parse_subject(APPM_HTML))
    cat.loaded |= {"CSCI", "APPM", "ECEN", "MATH", "ASEN"}
    return cat


def test_subject():
    c = make_catalog().courses
    assert c["CSCI 2270"].credits == 4 and satisfied(c["CSCI 2270"].prereq, {"ECEN 1310", "MATH 1300"})
    assert c["CSCI 2834"].coreq == ("c", "CSCI 2824")
    assert c["CSCI 1000"].restrictions and c["CSCI 1000"].prereq is None


def test_program():
    p = parse_program(PROGRAM_HTML)
    assert p.title.startswith("Computer Science")
    first = p.slots[0]
    assert first.options == [["CSCI 1000"], ["ASEN 1000"]], first
    pool = [s for s in p.slots if s.children][0]
    assert pool.pick == 2 and pool.credits == 6 and len(pool.children) == 2, pool
    assert pool.children[0].options == [["?Theory of Computation"]] and pool.children[1].options == [["CSCI 2834"]]
    assert p.slots[-1].label == "Free Elective" and p.slots[-1].credits == 6
    assert p.plan[0][0] == "First Year - Fall Semester" and p.plan[0][1][1][0] == "Natural Science Elective", p.plan


def test_program_requirement_pools_and_crosslists():
    html = """
    <h1>Astrophysical Sciences Minor</h1>
    <div id="requirementstextcontainer"><table class="sc_courselist"><tbody>
    <tr class="areaheader"><td>Elementary</td></tr>
    <tr><td>Select a maximum of two courses from the following:</td><td class="hourscol">6-8</td></tr>
    <tr><td class="codecol"><a title="ASTR 1030">ASTR 1030</a></td><td>Intro astronomy</td></tr>
    <tr><td class="codecol"><a title="ASTR 2500">ASTR 2500</a> / <a title="ASEN 1400">ASEN 1400</a></td><td>Gateway to Space</td></tr>
    <tr class="areaheader"><td>Advanced</td></tr>
    <tr><td>Select one of the following course sequences:</td></tr>
    <tr class="areasubheader"><td>Astrophysics Sequence:</td></tr>
    <tr><td class="codecol"><a title="ASTR 3730">ASTR 3730</a> &amp; <a title="ASTR 3830">ASTR 3830</a></td></tr>
    <tr class="areasubheader"><td>Planetary Sequence:</td></tr>
    <tr><td class="codecol"><a title="ASTR 3720">ASTR 3720</a> &amp; <a title="ASTR 3750">ASTR 3750</a></td></tr>
    <tr><td>Select a minimum of one of the following:</td></tr>
    <tr><td class="codecol"><a title="ASTR 3400">ASTR 3400</a></td></tr>
    </tbody></table></div>
    """
    program = parse_program(html)
    elementary, sequence, advanced = program.slots
    assert elementary.pick == 2 and len(elementary.children) == 2
    assert elementary.children[1].options == [["ASTR 2500"], ["ASEN 1400"]]
    assert sequence.pick == 1 and len(sequence.children) == 2
    assert sequence.children[0].options == [["ASTR 3730", "ASTR 3830"]]
    assert sequence.children[1].options == [["ASTR 3720", "ASTR 3750"]]
    assert advanced.pick == 1 and len(advanced.children) == 1


def test_plan():
    cat, p = make_catalog(), None
    prog = parse_program(PROGRAM_HTML)
    pl = Planner(cat, completed=["CSCI 1300", "APPM 1350"])
    required, generic = pl.expand(prog.slots)
    codes = [c for c, _ in required]
    assert "CSCI 1300" not in codes and "CSCI 3434" in codes and "CSCI 2834" in codes, codes
    allc, reasons = pl.close_over_prereqs(required)
    terms, stuck = pl.sequence(allc, reasons, generic, "2271", 16, 15)
    where = {n: i for i, t in enumerate(terms) for n, _, _ in t.items}
    assert not stuck, stuck
    assert where["CSCI 2270"] < where["CSCI 2400"] and where["CSCI 2270"] < where["CSCI 3104"]
    assert where["CSCI 2824"] <= where["CSCI 2834"]                      # corequisite may share a term
    assert where["CSCI 3104"] < where["CSCI 3434"]
    assert all(t.credits <= 16 for t in terms)


def test_meets_and_terms():
    assert cs.parse_meets("MWF 10:00am-10:50am") == [(0, 600, 650), (2, 600, 650), (4, 600, 650)]
    assert cs.parse_meets("TuTh 9:30am-10:45am, F 1:00pm-1:50pm")[0] == (1, 570, 645)
    assert cs.parse_meets("", '[{"meet_day":"1","start_time":"1530","end_time":"1645"}]') == [(1, 930, 1005)]
    assert cs.srcdb("Spring 2027") == "2271" and cs.srcdb("Fall 2026") == "2267"
    assert cs.previous_terms("2271", 3) == ["2267", "2261", "2257"]


def sec(code, no, kind, meets, stat="A"):
    return cs.Section(code, code, f"{code[-4:]}{no}", no, kind, stat, False, meets, "")


def test_schedules():
    mw = lambda a, b: [(0, a, b), (2, a, b)]
    data = {
        "AAAA 1000": [sec("AAAA 1000", "001", "LEC", mw(540, 590)), sec("AAAA 1000", "002", "LEC", mw(780, 830))],
        "BBBB 2000": [sec("BBBB 2000", "001", "LEC", mw(540, 590)), sec("BBBB 2000", "002", "LEC", mw(600, 650), "F")],
        "CCCC 3000": [sec("CCCC 3000", "001", "LEC", [(1, 600, 675)]), sec("CCCC 3000", "010", "REC", [(4, 600, 650)])],
    }
    ranked, problems = build_schedules(data, Prefs())
    assert len(ranked) == 1 and {s.no for s in ranked[0][1]} == {"002", "001", "010"} or ranked, problems
    chosen = {(s.code, s.no) for s in ranked[0][1]}
    assert ("AAAA 1000", "002") in chosen and ("BBBB 2000", "001") in chosen       # 001+001 clash; 002 is full
    ranked, _ = build_schedules(data, Prefs(include_closed=True))
    assert len(ranked) >= 2
    ranked, problems = build_schedules(data, Prefs(earliest=12 * 60))
    assert not ranked and problems


def test_schedules_are_distinct_timetables():
    mw = lambda a, b: [(0, a, b), (2, a, b)]
    data = {
        "AAAA 1000": [sec("AAAA 1000", "001", "LEC", mw(540, 590)), sec("AAAA 1000", "002", "LEC", mw(540, 590)),
                      sec("AAAA 1000", "003", "LEC", mw(780, 830))],
        "BBBB 2000": [sec("BBBB 2000", "001", "LEC", [(1, 600, 675)])],
    }
    ranked, _ = build_schedules(data, Prefs(max_results=60))
    times = [tuple(sorted(m for s in c for m in s.meets)) for _, c in ranked]
    assert len(ranked) == 2 and len(set(times)) == 2


def test_pinned_section_is_kept_and_others_fit_around_it():
    mw = lambda a, b: [(0, a, b), (2, a, b)]
    data = {
        "AAAA 1000": [sec("AAAA 1000", "001", "LEC", mw(540, 590)), sec("AAAA 1000", "002", "LEC", mw(780, 830))],
        "BBBB 2000": [sec("BBBB 2000", "001", "LEC", mw(540, 590)), sec("BBBB 2000", "002", "LEC", mw(780, 830)),
                      sec("BBBB 2000", "003", "LEC", mw(600, 650))],
    }
    free, _ = build_schedules(data, Prefs())
    assert any(s.no == "001" and s.code == "AAAA 1000" for s in free[0][1])
    ranked, _ = build_schedules(data, Prefs(), pins={"1000002"})
    assert ranked and all(any(s.crn == "1000002" for s in c) for _, c in ranked)
    assert {s.no for _, c in ranked for s in c if s.code == "BBBB 2000"} <= {"001", "003"}
    ranked, problems = build_schedules(data, Prefs(earliest=12 * 60), pins={"1000001", "2000002"})
    assert not ranked or all(s.crn in {"1000001", "2000002"} for _, c in ranked for s in c)
    closed = {"AAAA 1000": [sec("AAAA 1000", "001", "LEC", mw(540, 590), "F")]}
    ranked, _ = build_schedules(closed, Prefs(), pins={"1000001"})
    assert len(ranked) == 1


def test_blocked_sections_are_those_that_leave_no_schedule():
    mw = lambda a, b: [(0, a, b), (2, a, b)]
    data = {
        "AAAA 1000": [sec("AAAA 1000", "001", "LEC", mw(540, 590)), sec("AAAA 1000", "002", "LEC", mw(780, 830))],
        "BBBB 2000": [sec("BBBB 2000", "001", "LEC", mw(540, 590))],
    }
    assert gui._blocked_sections(data, Prefs(), []) == ["1000001"]
    assert gui._blocked_sections(data, Prefs(), ["1000002"]) == ["1000001"]


def test_three_component_course_links_through_lecture():
    def linked(code, no, kind, crn, links, meets):
        x = cs.Section(code, code, f"{code[-4:]}{no}", no, kind, "A", False, meets, "")
        x.crn = crn
        x.raw = {"crn": crn, "linked_crns": ",".join(links)}
        return x
    lec1 = linked("CCCC 1000", "001", "LEC", "1", ["2", "3"], [(0, 540, 590)])
    lec2 = linked("CCCC 1000", "002", "LEC", "4", ["5", "6"], [(0, 900, 950)])
    rec = linked("CCCC 1000", "011", "REC", "2", ["1"], [(1, 540, 590)])
    rec2 = linked("CCCC 1000", "012", "REC", "5", ["4"], [(1, 900, 950)])
    lab = linked("CCCC 1000", "021", "LAB", "3", ["1"], [(2, 540, 590)])
    lab2 = linked("CCCC 1000", "022", "LAB", "6", ["4"], [(2, 900, 950)])
    ranked, _ = build_schedules({"CCCC 1000": [lec1, lec2, rec, rec2, lab, lab2]}, Prefs())
    crns = sorted(sorted(str(s.crn) for s in c) for _, c in ranked)
    assert crns == [["1", "2", "3"], ["4", "5", "6"]]


def test_companion_lab_same_term():
    from cu_planner.catalog import Course
    cat = make_catalog()
    cat.courses["XXXX 1120"] = Course("XXXX 1120", "Lecture", 4.0, companion=("XXXX 1140",))
    cat.courses["XXXX 1140"] = Course("XXXX 1140", "Lab", 1.0)
    pl = Planner(cat, completed=[])
    terms, stuck = pl.sequence(["XXXX 1140", "XXXX 1120"], {}, [], "2271", 16, 6)
    where = {n: i for i, t in enumerate(terms) for n, _, _ in t.items}
    assert not stuck and where["XXXX 1120"] == where["XXXX 1140"]


def test_linked_zero_prefix_sections():
    d = {"ASEN 1030": [sec("ASEN 1030", n, k, [(i, 540 + 120 * i, 590 + 120 * i)])
                       for i, (n, k) in enumerate([("010", "LEC"), ("020", "LEC"), ("030", "LEC"),
                                                   ("011", "LAB"), ("021", "LAB"), ("031", "LAB")])]}
    ranked, _ = build_schedules(d, Prefs())
    assert ranked and all(c[0].no[:2] == c[1].no[:2] for _, c in ranked)
    assert len(ranked) == 3


def test_linked_crns_used_when_present():
    def mk(no, kind, crn, linked, day):
        x = sec("AAAA 1000", no, kind, [(day, 540, 590)])
        x.crn, x.raw = crn, {"linked_crns": linked}
        return x
    d = {"AAAA 1000": [mk("010", "LEC", "1", "9", 0), mk("020", "LEC", "2", "8", 1),
                       mk("011", "LAB", "8", "", 2), mk("021", "LAB", "9", "", 3)]}
    ranked, _ = build_schedules(d, Prefs())
    got = {frozenset(s.crn for s in c) for _, c in ranked}
    assert got == {frozenset({"1", "9"}), frozenset({"2", "8"})}, got


def test_linked_recitations():
    d = {"PHYS 1110": [sec("PHYS 1110", "100", "LEC", [(0, 540, 590)]), sec("PHYS 1110", "200", "LEC", [(0, 800, 850)]),
                       sec("PHYS 1110", "101", "REC", [(1, 800, 850)]), sec("PHYS 1110", "201", "REC", [(1, 900, 950)])]}
    ranked, _ = build_schedules(d, Prefs())
    assert ranked and all(({s.no[0] for s in c}) == {c[0].no[0]} for _, c in ranked)
    assert len(ranked) == 2


def test_registration():
    d = reg.parse_blocks(ENROLL_HTML)
    assert d.open_enrollment.isoformat() == "2027-01-04" and len(d.blocks) == 5
    d.term, d.source = "Spring 2027", "test"
    assert reg.block_for(75, d).label.startswith("Juniors") and reg.block_for(5, d).min_credits == 0 and reg.block_for(10, d).min_credits == 8
    assert reg.block_for(120, d).label.startswith("Seniors")
    assert "Juniors" in reg.describe(reg.KNOWN["2271"], 75, now=datetime(2026, 10, 24))
    path = reg.write_ics("/tmp/_cu_test.ics", reg.KNOWN["2271"], 75)
    assert "DTSTART:20261109T083000" in open(path).read()


def test_gui_helpers():
    assert gui._codes("CSCI 1300; APPM 1350\nPHYS 1110") == ["CSCI 1300", "APPM 1350", "PHYS 1110"]
    assert gui._mins("09:30", 0) == 570
    for value in ("24:00", "12:60", "not a time"):
        try:
            gui._mins(value, 0)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid time {value!r} to be rejected")
    try:
        gui.api_schedule({"term": "Spring 2027", "courses": ""})
    except ValueError as exc:
        assert "at least one course" in str(exc)
    else:
        raise AssertionError("Expected an empty schedule request to be rejected")


def test_non_json_class_search_response_is_reported():
    class Response:
        status_code = 202
        headers = {"Content-Type": "text/html; charset=UTF-8"}
        text = ""

        def raise_for_status(self):
            pass

    with tempfile.TemporaryDirectory() as cache_dir:
        old_cache = util.CACHE_DIR
        util.CACHE_DIR = Path(cache_dir)
        try:
            with patch.object(util._session, "post", return_value=Response()):
                try:
                    cs.subject_sections("2267", "ASEN")
                except cs.ClassSearchUnavailable as exc:
                    assert "temporarily unavailable" in str(exc)
                    assert "HTTP 202" in str(exc)
                    assert "JSONDecodeError" not in str(exc)
                else:
                    raise AssertionError("Expected an explicit Class Search availability error")
        finally:
            util.CACHE_DIR = old_cache


def test_class_search_request_matches_browser_alias_query():
    rows = [{"code": "ASEN 1010", "title": "Test Course", "crn": "12345", "stat": "A"}]
    with patch.object(cs, "post_json", return_value={"results": rows}) as post:
        sections = cs.course_sections("2271", "ASEN 1010")
    assert [section.code for section in sections] == ["ASEN 1010"]
    url, payload = post.call_args.args
    options = post.call_args.kwargs
    assert url.endswith("&alias=ASEN%201010")
    assert payload == {
        "other": {"srcdb": "2271"},
        "criteria": [{"field": "alias", "value": "ASEN 1010"}],
    }
    assert options["encoded_body"] is True
    assert options["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_encoded_post_json_wire_body():
    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"results":[]}'

        def raise_for_status(self):
            pass

    payload = {"other": {"srcdb": "2271"}, "criteria": [{"field": "alias", "value": "ASEN 1010"}]}
    with tempfile.TemporaryDirectory() as cache_dir:
        old_cache = util.CACHE_DIR
        util.CACHE_DIR = Path(cache_dir)
        try:
            with patch.object(util._session, "post", return_value=Response()) as post:
                assert util.post_json("https://classes.example/api", payload, encoded_body=True) == {"results": []}
            args, kwargs = post.call_args
            assert kwargs["data"] == (
                "%7B%22other%22%3A%7B%22srcdb%22%3A%222271%22%7D%2C%22criteria%22%3A"
                "%5B%7B%22field%22%3A%22alias%22%2C%22value%22%3A%22ASEN%201010%22%7D%5D%7D"
            )
            assert kwargs["headers"]["Content-Type"] == "application/json"
        finally:
            util.CACHE_DIR = old_cache


def test_combined_program_requirements():
    major = Program("Aerospace BS", "", [Slot("Gateway", [["ASEN 1400"]])], [])
    minor = Program("Astrophysical Sciences Minor", "", [
        Slot("Elementary", children=[Slot("Gateway", [["ASTR 2500"], ["ASEN 1400"]])])
    ], [])
    required, generic, programs, overlap = gui._merge_program_requirements([
        (major, [("APPM 1350", "Math"), ("ASEN 2501", "Major elective")], [("Technical elective", 3)]),
        (minor, [("APPM 1350", "Calculus"), ("ASTR 2100", "Core")], [("ASTR elective", 3)]),
    ], in_progress=["ASEN 1400"])
    assert [code for code, _ in required] == ["APPM 1350", "ASEN 2501", "ASTR 2100"]
    assert overlap == {
        "APPM 1350": ["Aerospace BS", "Astrophysical Sciences Minor"],
        "ASEN 1400": ["Aerospace BS", "Astrophysical Sciences Minor"],
    }
    assert programs["APPM 1350"] == {"Aerospace BS", "Astrophysical Sciences Minor"}
    assert len(generic) == 2


def test_three_programs_merge_with_shared_courses():
    a = Program("Major A", "", [], [])
    b = Program("Minor B", "", [], [])
    c = Program("Minor C", "", [], [])
    required, _, programs, overlap = gui._merge_program_requirements([
        (a, [("APPM 1350", "Math")], []),
        (b, [("APPM 1350", "Calc"), ("ASTR 2100", "Core")], []),
        (c, [("APPM 1350", "Calc"), ("APPM 3310", "Core")], []),
    ])
    assert [code for code, _ in required] == ["APPM 1350", "ASTR 2100", "APPM 3310"]
    assert programs["APPM 1350"] == {"Major A", "Minor B", "Minor C"}
    assert overlap["APPM 1350"] == ["Major A", "Minor B", "Minor C"]


def test_requisite_parsing_variants():
    from cu_planner.catalog import and_groups, parse_requisites, with_equivalents
    pre, co, conc, _ = parse_requisites(
        "Requires prerequisite course of GEEN 3830 (minimum grade C-) or prerequisite or corequisite course of "
        "APPM 1350 or MATH 1300 (minimum grade C-).")
    assert pre is None and satisfied(co, {"MATH 1300"}) and satisfied(co, {"GEEN 3830"}) and not satisfied(co, set())
    pre, co, _, _ = parse_requisites(
        "Requires prerequsite courses of PHYS 1110 or PHYS 1115 and a prerequisite or corequisite course of "
        "APPM 1360 or MATH 2300 (all minimum grade of C-).")
    assert satisfied(pre, {"PHYS 1115"}) and not satisfied(pre, {"APPM 1360"})
    assert satisfied(co, {"MATH 2300"}) and not satisfied(co, {"PHYS 1110"})
    pre, _, _, _ = parse_requisites("Requires prereqs ASEN 2403 or MCEN 2043 and APPM 2350 or MATH 2400 MATH 3430.")
    assert len(and_groups(pre)) == 3 and not satisfied(pre, {"ASEN 2403"})
    assert and_groups(("and", [("and", [("c", "A 1000"), ("c", "B 1000")]), ("c", "C 1000")])) == [
        ("c", "A 1000"), ("c", "B 1000"), ("c", "C 1000")]
    assert "APPM 1350" in with_equivalents({"MATH 1300"})


def test_finish_year_horizon_and_elective_double_credit():
    from cu_planner.gui import _double_credit_options
    cat = Catalog()
    cat.courses["MATH 2300"] = Course("MATH 2300", "Calculus 2", 4.0)
    cat.courses["APPM 1360"] = Course("APPM 1360", "Calculus 2 for Engineers", 4.0)
    cat.courses["CSCI 1300"] = Course("CSCI 1300", "Intro Programming", 4.0)
    cat.loaded.update({"MATH", "APPM", "CSCI"})
    pl = Planner(cat)
    terms, stuck = pl.sequence(["CSCI 1300"], {}, [], "2271", 16, 15, max_terms=1)
    assert not stuck and len(terms) == 1 and terms[0].items[0][0] == "CSCI 1300"
    terms, stuck = pl.sequence(["CSCI 1300", "MATH 2300"], {}, [], "2271", 4, 4, max_terms=1)
    assert len(stuck) == 1 and len(terms) == 1
    assert _double_credit_options("MATH 2300", {
        "MATH 2300": ["Math BS"], "APPM 1360": ["Aerospace BS"]}) == [
        {"code": "APPM 1360", "programs": ["Aerospace BS"]},
        {"code": "MATH 2300", "programs": ["Math BS"]}]


def test_courses_never_precede_recommended_term():
    cat = Catalog()
    for code in ("APPM 2350", "APPM 2360"):
        cat.courses[code] = Course(code, code, 4.0)
    cat.loaded.add("APPM")
    terms, stuck = Planner(cat).sequence(["APPM 2350", "APPM 2360"], {}, [], "2277", 24, 8,
                                         rank={"APPM 2360": 0, "APPM 2350": 1})
    where = {c: i for i, t in enumerate(terms) for c, _, _ in t.items}
    assert not stuck and where == {"APPM 2360": 0, "APPM 2350": 1}, where


def test_equivalents_and_balance():
    from cu_planner.catalog import with_equivalents
    from cu_planner.gui import _elective_kind
    assert {"APPM 1350", "MATH 1300"} <= with_equivalents({"MATH 1300"})
    assert "APPM 2360" in with_equivalents({"MATH 2130", "MATH 3430"})
    assert _elective_kind("Technical Elective") == "technical"
    assert _elective_kind("Humanities/Social Science Elective") == "hss"
    cat = Catalog()
    for code, pre in [("MATH 1300", None), ("MATH 2300", ("c", "APPM 1350"))]:
        cat.courses[code] = Course(code, code, 4.0, "", pre)
    cat.loaded.add("MATH")
    pl = Planner(cat, ["APPM 1350"])
    terms, stuck = pl.sequence(["MATH 2300"], {}, [("Free Elective", 3.0, 0)] * 6, "2277", 16, 12)
    assert not stuck and "MATH 2300" in [n for t in terms for n, _, _ in t.items]
    loads = [t.credits for t in terms]
    assert max(loads) - min(loads[:-1] or loads) <= 4, loads


def test_recommended_rank():
    from cu_planner.gui import _recommended_rank
    prog = Program("P", "u", [], [("Year One - Fall Semester", [("MATH 1300 Calc", 5)]),
                                  ("Year One - Spring Semester", [("MATH 2300 Calc 2", 5)]),
                                  ("Year Two - Fall Semester", [("PHYS 1110 Physics", 4)])])
    assert _recommended_rank([prog], set(), "2267") == {"MATH 1300": 0, "MATH 2300": 1, "PHYS 1110": 2}
    assert _recommended_rank([prog], {"MATH 1300"}, "2271") == {"MATH 1300": 0, "MATH 2300": 0, "PHYS 1110": 1}


def test_in_progress_term():
    catalog = make_catalog()
    term = gui._in_progress_term("2267", ["CSCI 1300", "CSCI 2270"], catalog)
    assert term.db == "2267"
    assert term.credits == 8
    assert [item[2] for item in term.items] == ["in progress", "in progress"]
    assert gui._next_full_term("2267") == "2271"
    assert gui._next_full_term("2271") == "2277"
    assert gui._next_full_term("2264") == "2267"


def test_semester_suggestions_fall_back_to_catalog():
    def unavailable(*a, **k):
        raise cs.ClassSearchUnavailable("blocked")
    with patch.object(cs, "term_sections", unavailable), patch.object(cs, "subject_sections", unavailable):
        out = gui.api_semester({"term": "Fall 2026", "courses": "", "done": "", "slots": [{"kind": "hss"}]})
    assert out["live"] is False
    assert out["suggestions"]["hss"]["options"], "catalog-only suggestions should still be offered"
    assert all("sections" not in o for o in out["suggestions"]["hss"]["options"])


def test_audit_import_fills_elective_slots():
    from cu_planner.audit import parse_audit
    text = ("Coursework History\nFA23ARSC1000 3.0TA Expository Writing\nFL-SEU: ENGL1133\n"
            "FA23HUMN1999TC 3.0TA Western Cultural History 1\nFL-SEU: HUMS1023\n"
            "FA26PHYS1110 4.0*** General Physics 1\nFA26MATH1300 3.0F Failed\n")
    a = parse_audit(text)
    assert a["completed"] == ["ENGL 1133", "HUMS 1023"] and a["in_progress"] == ["PHYS 1110"]
    assert a["earned_hours"] == 6.0 and a["in_progress_hours"] == 4.0
    base = {"programs": ["Aerospace Engineering Sciences"], "start": "Spring 2027", "finish_years": 4}
    before = gui.api_plan(base)
    after = gui.api_plan({**base, "completed": a["completed"], "in_progress": a["in_progress"]})
    assert after["total_credits"] <= before["total_credits"] - 6
    assert {e["code"] for e in after["earned_credit"]} >= {"ENGL 1133", "HUMS 1023"}


def test_state_is_saved_per_visitor():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d, patch.object(gui, "STATE_DIR", Path(d) / "users"):
        alice, bob = "a" * 24, "b" * 24
        assert gui.load_state(alice) == {}
        gui.save_state(alice, {"progs": [{"label": "Alice major"}]})
        gui.save_state(bob, {"progs": [{"label": "Bob major"}]})
        assert gui.load_state(alice)["progs"][0]["label"] == "Alice major"
        assert gui.load_state(bob)["progs"][0]["label"] == "Bob major"
        for bad in ("../../etc/passwd", "short", ""):
            try:
                gui.load_state(bad) if False else gui._state_path(bad)
            except ValueError:
                continue
            raise AssertionError("path-like visitor ids must be rejected")
        try:
            gui.save_state(alice, [1])
        except ValueError:
            pass
        else:
            raise AssertionError("non-object state should be rejected")


def test_credit_pick_lists_are_not_all_required():
    from cu_planner.catalog import parse_program
    def row(code, hrs=""):
        return f'<tr><td class="codecol"><a title="{code}">{code}</a></td><td>T</td><td class="hourscol">{hrs}</td></tr>'
    html = ('<h1>X - Minor</h1><div id="requirementstextcontainer"><table class="sc_courselist">'
            + row("ECEN 2250", "3")
            + '<tr><td colspan="2">Complete 9 credits, chosen from the following:</td><td class="hourscol">9</td></tr>'
            + "".join(row(f"ECEN {n}") for n in (2350, 2360, 2370, 3170, 3250))
            + '<tr class="areaheader"><td colspan="2">Electives</td><td class="hourscol">6</td></tr>'
            + "".join(row(f"ARAB {n}") for n in (3120, 3220, 3221))
            + "</table></div>")
    slots = parse_program(html).slots
    pools = [x for x in slots if x.children]
    assert [(x.pick, len(x.children)) for x in pools] == [(3, 5), (2, 3)], [(x.label, x.pick) for x in slots]
    assert sum(1 for x in slots if x.options) == 1


def test_elective_lists_follow_the_audit():
    assert gui._allowed("writing", "WRTG 3030") and gui._allowed("writing", "ENES 1010") and gui._allowed("writing", "PHYS 3050")
    assert not gui._allowed("writing", "WRTG 1150")
    assert gui._allowed("mathsci", "ASTR 2100") and not gui._allowed("mathsci", "ASTR 1020")
    assert gui._allowed("technical", "PHYS 2130") and not gui._allowed("technical", "PHYS 2020")
    assert gui._allowed("technical", "CSCI 2270") and not gui._allowed("technical", "CSCI 1300")
    assert gui._allowed("aero", "ASEN 4519") and not gui._allowed("aero", "ASEN 3300")
    assert gui._earned_kinds("WRTG 3030")[0] == "writing" and "writing" not in gui._earned_kinds("WRTG 1150")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
