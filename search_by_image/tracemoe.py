"""trace.moe: which anime, episode and moment a screenshot comes from.

Official API, no key needed: https://soruly.github.io/trace.moe-api/
Without a key the quota is small (100 searches, counted per IP address) and
only one search may run at a time, so the page asks for it only when the user
ticks the anime box.

trace.moe always returns its closest frames, even for a photo that has nothing
to do with anime; those come back around 80 % similar. Its documentation
says matches below 90 % are usually wrong, so they are dropped.
"""

from __future__ import annotations

import typing as t
from urllib.parse import quote

from searx import network

API_URL = "https://api.trace.moe/search"
MIN_SIMILARITY = 0.9
MAX_RESULTS = 5

_STATUS_ERRORS = {
    402: "the free searches for this connection are used up for now",
    429: "only one search at a time is allowed; try again in a moment",
    503: "overloaded; try again later",
}


class TraceMoeError(Exception):
    """trace.moe refused the request or answered with something unexpected."""


def search(
    image: bytes | None, mime: str | None, url: str | None, timeout: float, hide_adult: bool
) -> dict[str, t.Any]:
    """Search by an uploaded ``image`` or an image ``url``."""
    endpoint = f"{API_URL}?anilistInfo&cutBorders"
    if image is not None:
        resp = network.post(
            endpoint,
            data=image,
            headers={"Content-Type": mime or "application/octet-stream"},
            timeout=timeout,
            raise_for_httperror=False,
        )
    else:
        resp = network.get(f"{endpoint}&url={quote(url or '', safe='')}", timeout=timeout, raise_for_httperror=False)

    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        if resp.status_code == 403 and image is None:
            raise TraceMoeError("couldn't download the image from that link")
        message = _STATUS_ERRORS.get(resp.status_code) or body.get("error") or f"HTTP {resp.status_code}"
        raise TraceMoeError(message)

    matches = []
    seen = set()
    for item in body.get("result", []):
        anilist = item.get("anilist")
        if item.get("similarity", 0) < MIN_SIMILARITY or not isinstance(anilist, dict):
            continue
        if hide_adult and anilist.get("isAdult"):
            continue
        key = (anilist.get("id"), item.get("episode"))
        if key in seen:
            continue
        seen.add(key)
        titles = anilist.get("title") or {}
        matches.append(
            {
                "title": titles.get("english") or titles.get("romaji") or titles.get("native") or item.get("filename"),
                "native_title": titles.get("native") or "",
                "episode": item.get("episode"),
                "time": _timestamp(item.get("from")),
                "similarity": round(item["similarity"] * 100, 1),
                "frame": item.get("image"),
                "anilist_url": anilist.get("siteUrl") or f"https://anilist.co/anime/{anilist.get('id')}",
                "year": anilist.get("seasonYear") or (anilist.get("startDate") or {}).get("year"),
                "format": anilist.get("format") or "",
            }
        )
        if len(matches) == MAX_RESULTS:
            break

    quota_left = None
    if isinstance(body.get("quota"), int) and isinstance(body.get("quotaUsed"), int):
        quota_left = max(body["quota"] - body["quotaUsed"], 0)
    return {"matches": matches, "quota_left": quota_left}


def _timestamp(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
