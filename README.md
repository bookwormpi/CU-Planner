# CU Planner

CU Planner helps University of Colorado Boulder students explore degree requirements, build
term-by-term course plans, find conflict-free class schedules, and look up registration windows.
It uses the public CU Boulder catalog, Class Search, and registrar information.

## Important: vibe-coded project and security disclaimer

**This project is vibe-coded. No guarantees are given about its security, privacy, reliability,
or fitness for any particular purpose. Use it at your own risk.**

The application may contain bugs or security vulnerabilities. Do not assume it protects sensitive
information or credentials. Be especially careful when importing degree audits or providing browser
cookies, and do not expose the application to untrusted networks without independently assessing
and securing it.

Plans, schedules, and registration information may be incomplete or incorrect. This tool is not a
substitute for official CU Boulder systems or academic advising. Verify important decisions with
official sources and your advisor.

## Features

- Combine majors, minors, and certificates in a term-by-term plan.
- Account for completed and in-progress courses, prerequisites, corequisites, credit limits, and
  known term offerings.
- Explore electives, adjust the plan manually, and identify potential requirement overlaps.
- Find non-overlapping class sections and filter by time, days, and availability.
- Estimate registration windows and optionally export a calendar file.
- Use a local browser interface or the command line.

## Getting started

### Requirements and installation

You need Python, pip, and an internet connection to fetch current catalog and class information.
From the project directory, install the dependencies:

```sh
python -m pip install -r requirements.txt
```

### Launch the graphical interface

```sh
python -m cu_planner gui
```

The app opens in your browser and listens locally at `http://127.0.0.1:8765`. To use another port,
run `python -m cu_planner gui --port 8766`. Stop the server with Ctrl+C in the terminal.

## Using the graphical interface

1. Search for and select one or more majors, minors, or certificates.
2. Set your start term, completion target, and planning preferences. The default completion target
   is four years; the allowed range is one to ten years.
3. Enter completed and in-progress courses. In Settings, you can also paste your degree audit from
   the Audit Results tab and select **Import audit**. For AP, IB, or dual credit, use the
   CU-equivalent course codes shown on your credit evaluation.
4. Review the generated plan. Drag course cards or use their arrow buttons to move courses between
   terms. Manual moves do not automatically resequence the plan; prerequisite and term-offering
   conflicts are flagged.
5. Use the elective placeholders to search for courses, then use a term's scheduling controls to
   build a timetable. In the Schedule tab, select or pin sections to refit the other courses around
   them. Pins override the closed-section and time-window filters.

The plan rebuilds when programs or planning settings change. Shared required courses are scheduled
once, while elective pools remain separate unless the catalog identifies the same specific course
for both. An already planned course can be selected to satisfy an eligible elective without
counting its credits twice. Confirm all course sharing and double-counting with your advisor.

The planner balances credits across available terms and reports required courses or elective
credits that do not fit within the chosen completion target and credit limit. The start term counts
toward that target, including when it contains in-progress courses. Catalog term order is a
preference, not a hard constraint. Some equivalent courses, such as APPM and MATH calculus courses
on the same topic, are treated as equivalent for prerequisites and requirements.

## Command-line usage

Run `python -m cu_planner --help` for the available commands, or append `--help` to a command for
its options. The following examples use Spring 2027; replace terms and courses as appropriate.

### Build a degree plan

```sh
python -m cu_planner plan --program "computer science bs" --start "Spring 2027" \
  --completed "CSCI 1300, APPM 1350, PHYS 1110"
```

`--program` accepts search words or a catalog URL. If a search returns multiple matches, rerun with
`--pick N` or use the desired URL. Use `--prefer` to favor alternatives, `--max-credits` to set the
credit cap, and `--infer-offerings` to infer seasonal offerings from previous Class Search terms.

### Find class schedules

```sh
python -m cu_planner schedule --term "Spring 2027" \
  --courses "CSCI 2270, APPM 1360, PHYS 1120" --earliest 9:00 --avoid-days F
```

Other options include `--latest`, `--include-closed`, and `--results`. In `--avoid-days`, use
`M T W R F`, where `R` means Thursday. Confirm lecture, recitation, and lab combinations in Class
Search before registering.

### Look up registration dates

```sh
python -m cu_planner register --term "Spring 2027" --credits 62 --ics register.ics
```

Credits are completed hours, including transfer and AP credit. Spring 2027 dates are built in; for
other terms, provide the registrar's **Continuing Student Enrollment Dates** page with
`--enroll-url`. The result identifies the start of your credit block, not your exact appointment.
Find the latter in Buff Portal; optionally provide it with
`--appointment "2026-11-09 08:45"` when exporting an `.ics` file.

### Run the combined workflow

```sh
python -m cu_planner go --program "computer science bs" --start "Spring 2027" \
  --credits 62 --completed "CSCI 1300, APPM 1350"
```

This builds a plan, searches for schedules for its first planned term, and looks up registration
dates.

## Data sources and storage

| Component | Source or behavior |
| --- | --- |
| Requirements and prerequisites | CU Boulder catalog program and course pages |
| Course sections | CU Boulder Class Search |
| Registration dates | CU Boulder registrar enrollment-date posts; Spring 2027 data is built in |
| Electives | CU engineering and aerospace elective lists and Class Search attributes; built-in lists are used when a source is unavailable |

The GUI saves page state, including imported audit text and plan settings, per browser visitor under
`~/.config/cu_planner/users/` by default. `CU_PLANNER_STATE_DIR` changes this directory. Catalog and
Class Search responses are cached in memory and under `~/.cache/cu_planner`; stale cached responses
may be used when a site is unavailable. Failed pages are not retried for ten minutes.

Class Search may block scripted requests with a bot challenge. If live sections are unavailable,
the GUI can use catalog-only suggestions. The **Connect Class Search** control accepts a browser
`Cookie` header for the current process; that cookie is kept in memory. Treat it as a credential and
only provide it if you understand the risk.

## Limitations

- Live endpoints are untested; offline parser tests use fixtures shaped like the catalog, Class
  Search, and registrar pages. External site changes may break scraping or make results incomplete.
- Major or standing restrictions, minimum-grade rules, and capstone credit thresholds are not
  modeled. Unsatisfied catalog prerequisites that no listed course can satisfy are skipped and
  flagged. Pre-calculus placement is assumed unless you indicate otherwise.
- Lecture, recitation, and lab pairings may be inferred from section numbers. Verify the combination
  and current availability in Class Search.
- Registration estimates are based on credit blocks. Your individual appointment may be later;
  check Buff Portal.
- The tool does not determine whether every shared course or elective is accepted by your programs.
  Confirm requirements and applicability with your advisor.

## Testing and troubleshooting

Run the offline tests from the project directory:

```sh
python tests/test_offline.py
```

If a scraper stops working:

- For catalog pages, inspect the program page's requirements table and update `parse_program` in
  [`cu_planner/catalog.py`](cu_planner/catalog.py) and the related offline fixtures.
- For Class Search, compare the browser's network request and response with `_search()` and
  `_section_from()` in [`cu_planner/classsearch.py`](cu_planner/classsearch.py).
- Cached responses live in `~/.cache/cu_planner`; removing the relevant cached data forces a fresh
  download on the next request.
