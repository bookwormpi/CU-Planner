"""Approved-course lists taken straight from CU Boulder's own pages (nothing here is hand-maintained when the sites respond).

* Class Search attributes  ENGR GenEd: Writing Requirement (engrgened_EWR) / Humanities & Social Science (engrgened_HSS)
* College of Engineering "Humanities, Social Sciences and Writing Requirements" page (extra approved courses)
* Aerospace "Math, Science, & Approved Technical Electives" page
"""
import re

from .classsearch import ClassSearchUnavailable, _search, previous_terms, srcdb
from .util import get_html

HSS_PAGE = "https://www.colorado.edu/engineering-advising/get-your-degree/degree-requirements/humanities-social-sciences-and-writing-requirements"
AERO_PAGE = "https://www.colorado.edu/aerospace/academics/undergraduates/math-science-technical-electives"
ALL_PREFIXES = ("ENES", "ENLP", "EHON")   # every course with these prefixes counts for H&SS
TERMS_BACK = 6                           # Class Search only lists what a term offers, so union several terms
_MEMO = {}


def _text(url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(get_html(url, ttl=7 * 24 * 3600), "html.parser")
    return (soup.find("main") or soup).get_text("\n", strip=True)


def _attribute_codes(field, term):
    codes = set()
    terms = [srcdb(term)] + previous_terms(srcdb(term), TERMS_BACK)
    for db in terms:
        try:
            rows = _search(db, [{"field": field, "value": "Y"}])
        except (ClassSearchUnavailable, RuntimeError):
            continue
        codes |= {r["code"] for r in rows if r.get("code")}
    return codes


def _page_courses(section):
    """Course codes listed on the college page, e.g. 'ARCH 3114 — ...', 'MUEL/MUSC 3642 — ...'."""
    out = set()
    for line in section.split("\n"):
        head = line.split("—")[0]
        if "—" not in line or not re.match(r"[A-Z]{4}", head):
            continue
        subjects, subject = [], None
        for tok in re.findall(r"[A-Z]{4}|\d{4}", head):
            if tok.isdigit():
                out.update(f"{s} {tok}" for s in subjects)
            else:
                subjects = subjects + [tok] if subject and not head.split(tok)[0].strip().rstrip("/").endswith(tuple("0123456789")) else [tok]
                subject = tok
    return out


def hss_courses(term="Spring 2027"):
    """-> set of 'SUBJ NNNN' codes approved for Humanities & Social Sciences (plus ALL_PREFIXES, handled by the caller)."""
    if "hss" not in _MEMO:
        codes = _attribute_codes("engrgened_HSS", term)
        try:
            text = _text(HSS_PAGE)
            start = text.find("so approved:")
            codes |= _page_courses(text[start:text.find("Approved ROTC", start)] if start >= 0 else "")
        except Exception:
            pass
        _MEMO["hss"] = codes
    return _MEMO["hss"]


def writing_courses(term="Spring 2027"):
    if "writing" not in _MEMO:
        _MEMO["writing"] = _attribute_codes("engrgened_EWR", term)
    return _MEMO["writing"]


def aerospace_lists():
    """-> {'technical': {SUBJ: [(lo, hi)]}, 'mathsci': {...}} read from the Aerospace electives page, or {} on failure."""
    if "aero" in _MEMO:
        return _MEMO["aero"]
    out = {}
    try:
        text = _text(AERO_PAGE)
        head, _, tail = text.partition("Approved Math and Science Electives")
        technical, mathsci = {}, {}
        for line in head.split("\n"):
            m = re.match(r"([A-Z]{4})\s?(.*)$", line.strip())
            if m and not line.endswith(":"):
                rest = re.sub(r"\(.*?\)", "", m[2])
                for n in re.finditer(r"(\d{4})(\+)?", rest):
                    technical.setdefault(m[1], []).append((int(n[1]), 9999 if n[2] else int(n[1])))
        for m in re.finditer(r"^([A-Z]{4}) (\d{4})\n", tail, re.M):
            mathsci.setdefault(m[1], []).append((int(m[2]), int(m[2])))
        if technical and mathsci:
            out = {"technical": technical, "mathsci": mathsci}
    except Exception:
        out = {}
    _MEMO["aero"] = out
    return out
