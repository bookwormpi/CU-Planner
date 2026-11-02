# cu_planner

Plans a CU Boulder degree, minor or certificate from the public catalog, builds conflict-free
schedules from Class Search, and tells you when your registration window opens.

```
pip install -r requirements.txt

# Easiest: the graphical interface (opens in your browser, runs only on your machine)
python -m cu_planner gui

# Or use the command line:

# 1. Term-by-term plan that respects prerequisites (search words or a catalog URL)
python -m cu_planner plan --program "computer science bs" --start "Spring 2027" \
       --completed "CSCI 1300, APPM 1350, PHYS 1110"

# 2. Section combinations with no time conflicts
python -m cu_planner schedule --term "Spring 2027" \
       --courses "CSCI 2270, APPM 1360, PHYS 1120" --earliest 9:00 --avoid-days F

# 3. When can I register?  (credits = completed hours incl. transfer/AP)
python -m cu_planner register --term "Spring 2027" --credits 62 --ics register.ics

# Or all three: plan -> schedules for the first planned term -> registration dates
python -m cu_planner go --program "computer science bs" --credits 62 --completed "..."
```

In the GUI, add any number of majors, minors and certificates as chips (search, then pick each;
× removes one) to build a combined plan. Courses
required by both are scheduled once and identified as shared; elective pools remain separate
unless the catalog identifies the same specific course for both. Confirm that course sharing
is allowed by both departments. Enter courses in progress in the selected start term; those
courses appear in that term and count as completed for remaining requirements and prerequisites.
In Settings, paste the text of your degree audit (Audit Results tab → select all → copy) and click
Import audit to fill in earned, transfer and in-progress courses. Earned or in-progress courses that
aren't named requirements (transfer humanities, honors, etc.) fill matching elective slots. For AP/IB/dual
credit, enter the CU-equivalent course codes shown on your credit evaluation.
The plan rebuilds automatically when you change programs, start term, years, focus area or settings
(with a progress overlay). Your whole page (programs, settings, imported credit, dragged plan, term
schedule inputs) is saved per visitor: each browser gets a private random cookie and its own file under
`~/.config/cu_planner/users/`, so every person sees their own page on each visit. Catalog and Class
Search responses are cached on disk (`~/.cache/cu_planner`) and in memory; failed pages aren't retried
for 10 minutes, and stale cache is used if a site is down.
Drag a course card between term columns to rearrange your plan. The arrow buttons on each card
do the same thing without dragging; term credit totals update immediately, and “Pick classes”
uses the courses currently shown in that term. Manual moves don't automatically re-sequence courses;
the board flags prerequisite and term-offering conflicts.

If `--program` words match several pages the tool lists them; pick one with `--pick N` or paste the
catalog URL directly. For a minor, search for it the same way ("computer science minor").

## How it works

| Piece | Source | Notes |
|---|---|---|
| Requirements | program page on catalog.colorado.edu (`table.sc_courselist`) | "or" rows become alternatives, "pick N" lists become pools |
| Prerequisites | `catalog.colorado.edu/courses-a-z/<subject>/` | `(A or B) and (C or D)` parsed into a tree; prerequisites missing from your program (e.g. a calculus lead-in) are added automatically |
| Sequencing | `planner.py` | Greedy by longest downstream chain, honoring prerequisites, corequisites, a credit cap, and Fall/Spring offering when known and never placing a course before the term the catalog's recommended plan puts it in |
| Sections | `classes.colorado.edu/api/?page=fose&route=search` | One lecture/recitation/lab per course, no overlaps, ranked by gaps, campus days and early starts |
| Registration | registrar "Continuing Student Enrollment Dates" post | Spring 2027 dates are built in; other terms: `--enroll-url <post>` |

Useful flags: `--prefer "ECEN 1310"` (pick that alternative), `--infer-offerings` (learns which courses
run only Fall or only Spring from the last four terms of Class Search), `--include-closed`,
`--max-credits`, `--appointment "2026-11-09 08:45"` (your exact Buff Portal time, for the .ics).

## Limits you should know about

- **Live endpoints are untested.** The parsers are checked offline against fixtures shaped like the
  CourseLeaf catalog, the Class Search JSON and the registrar post (`python tests/test_offline.py`).
  If a live call returns nothing, see below.
- Not modeled: major/standing restrictions, minimum-grade rules, and capstone credit thresholds.
  Catalog prerequisites that no listed course can satisfy are skipped (and flagged). Pre-calculus
  placement is assumed unless you tick "I still need pre-calculus". Confirm with your advisor.
- **One page.** The GUI builds the whole degree (default: finish in 4 years, 16-18 credits/term,
  pick a focus area), then "Plan this semester" on any term builds a timetable and suggests
  electives that fit it (double-counting ones first). "Add to plan" fills the slot.
- Class Search blocks scripts with a bot challenge. If live sections are unavailable, suggestions
  use the catalog only. To get live data, paste your browser's `Cookie` header into the
  "Connect Class Search" box (kept in memory only).
- Your exact appointment time is only in Buff Portal. The tool gives your block's first slot.

## If a scraper breaks

- Catalog tables: save the program page HTML, adjust `parse_program` in `catalog.py`, and update the
  fixture in `tests/test_offline.py` first so you can see it pass.
- Class Search: in the browser, open DevTools -> Network, run a search at classes.colorado.edu, and
  compare the request body and result keys with `_search()` / `_section_from()` in `classsearch.py`.
  Status codes (`A` = open) and meeting-time fields are the likeliest differences.
- Cached pages live in `~/.cache/cu_planner`; delete it to force fresh downloads.

Terms are balanced: remaining credits are spread evenly across the terms the prerequisite chains allow, rather than front-loading. Elective requirements from the catalog plan (humanities, technical, free, ...) appear as dashed "Choose a course" boxes; open one to filter a course list and hover an option to see its description, credits, prerequisites and typical terms. A lecture and the lab/recitation course its catalog entry says is normally taken with it (e.g. PHYS 1120 + PHYS 1140) are placed in the same term. In the Schedule tab, recitation/lab sections are only paired with lectures whose section numbers match (100 with 101-104) when the data supports that. In the Schedule tab's table, change a course's section with its dropdown or click its 📌 to pin the current one; the other courses are re-fitted around it. A pin overrides the closed/time-window filters, is saved per visitor, and "Clear" in the pinned-sections line removes them all. APPM/MATH calculus courses on the same topic (e.g. APPM 1350 = MATH 1300) are treated as equivalent for prerequisites and requirements.

The degree planner has a configurable completion target (1–10 years, default six for the current combined BSAE + APS-minor plan). The limit counts the start term; if courses are in progress, they occupy that term. Courses are prioritized over elective placeholders, catalog term order is a preference rather than a hard constraint, and empty terms are omitted. If the selected horizon cannot fit required courses or elective credits at the per-term credit cap, the plan reports the remaining work. Elective search results identify courses equivalent to already planned program requirements as possible double-credit options; selecting one already in the plan marks the elective as satisfied by that course without counting its credits twice. Confirm applicability with an advisor.

## Elective lists

The elective dropdowns use CU's own lists: Class Search's "ENGR GenEd" attributes (writing and Humanities & Social
Sciences, unioned over the last several terms), the College of Engineering H&SS page, and the Aerospace department's
math/science/technical elective page (`cu_planner/official.py`). If a source can't be reached, the built-in copy is used.
