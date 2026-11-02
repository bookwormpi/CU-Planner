"""HTTP helpers: one shared session, polite delays, and a small on-disk cache."""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote

import requests

CACHE_DIR = Path.home() / ".cache" / "cu_planner"
_session = requests.Session()
_session.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

def set_cookie(value):
    """Let the user pass along a browser session cookie for sites that challenge scripted requests (memory only)."""
    value = " ".join(str(value or "").split()).strip()
    if value:
        _session.headers["Cookie"] = value
    else:
        _session.headers.pop("Cookie", None)
    return bool(value)


_memory = {}       # path -> (mtime, text): skips disk reads and re-hashing on repeat requests
_failures = {}     # key -> (time, error): don't hammer pages that just failed (e.g. a 404 subject)
FAILURE_TTL = 600


def _cached(kind, key_parts, ttl, fetch):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1("|".join(map(str, key_parts)).encode()).hexdigest()
    path = CACHE_DIR / f"{kind}-{digest}.txt"
    now = time.time()
    hit = _memory.get(path)
    if hit and now - hit[0] < ttl:
        return hit[1]
    stale = None
    if path.exists():
        mtime = path.stat().st_mtime
        text = path.read_text(encoding="utf-8")
        if now - mtime < ttl:
            _memory[path] = (mtime, text)
            return text
        stale = text
    failed = _failures.get(digest)
    if failed and now - failed[0] < FAILURE_TTL:
        if stale is not None:
            return stale
        raise failed[1]
    try:
        text = fetch()
    except Exception as exc:
        _failures[digest] = (now, exc)
        if stale is not None:                         # an old copy beats an error when the site is down
            return stale
        raise
    _failures.pop(digest, None)
    path.write_text(text, encoding="utf-8")
    _memory[path] = (now, text)
    return text


def get_html(url, ttl=24 * 3600):
    def go():
        time.sleep(0.4)
        r = _session.get(url, timeout=30)
        r.raise_for_status()
        return r.text
    return _cached("html", [url], ttl, go)


def post_json(url, payload, ttl=1800, *, encoded_body=False, headers=None):
    body = json.dumps(payload, separators=(",", ":"))
    if encoded_body:
        body = quote(body, safe="~()*!.'-")

    def go():
        request_headers = {"Content-Type": "application/json"} if encoded_body else {}
        request_headers.update(headers or {})

        for attempt in range(4):
            time.sleep(0.8 if attempt == 0 else 1.5)

            if encoded_body:
                r = _session.post(
                    url, data=body, headers=request_headers, timeout=60
                )
            else:
                r = _session.post(
                    url, json=payload, headers=request_headers or None, timeout=60
                )

            # CU's API may return a temporary empty 202 response.
            if (r.status_code == 202 or not r.text.strip()) and attempt < 3:
                continue

            r.raise_for_status()

            try:
                json.loads(r.text)
            except json.JSONDecodeError as exc:
                content_type = r.headers.get("Content-Type", "unknown")
                preview = " ".join(r.text.split())[:180]
                raise RuntimeError(
                    f"Expected JSON from {url}, but received {content_type} "
                    f"(HTTP {r.status_code}): {preview or '[empty response]'}"
                ) from exc

            return r.text

        raise RuntimeError(
            f"CU API returned HTTP {r.status_code} with an empty or "
            f"unavailable response after retries: {url}"
        )

    key_parts = [
        url,
        json.dumps(payload, sort_keys=True),
        encoded_body,
        json.dumps(headers or {}, sort_keys=True),
    ]
    try:
        return json.loads(_cached("json", key_parts, ttl, go))
    except json.JSONDecodeError:
        digest = hashlib.sha1("|".join(map(str, key_parts)).encode()).hexdigest()
        (CACHE_DIR / f"json-{digest}.txt").unlink(missing_ok=True)
        return json.loads(_cached("json", key_parts, ttl, go))
