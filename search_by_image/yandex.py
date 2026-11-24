"""Yandex reverse image search.

Yandex has no public API for this. The plugin uses the same two requests as
yandex.com's own page: an upload that returns an ID (``cbir_id``) for the image,
then the results page for that ID, whose data sits as JSON in a
``data-state`` attribute. When the user gives an image link instead of a file,
the results page takes the link directly.

The upload response also names a public copy of the image on Yandex's image
host. The results page offers it to the other search sites (Google Lens, Bing,
TinEye, SauceNAO), which only accept a link: those sites block servers, so the
user's own browser has to open them.
"""

from __future__ import annotations

import html
import json
import re
import typing as t
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi import CurlMime

from searx import network

SEARCH_URL = "https://yandex.com/images/search"
_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
_UPLOAD_REQUEST = json.dumps({"blocks": [{"block": "b-page_type_search-by-image__link"}]})
_STATE_RE = re.compile(r'data-state="([^"]*)"')
# Size groups from the "other sizes" list, largest first.
_SIZE_GROUPS = ("large_dups", "medium_dups", "small_dups")


class YandexError(Exception):
    """Yandex refused the request or answered with something unexpected."""


def search(image: bytes | None, mime: str | None, url: str | None, timeout: float) -> dict[str, t.Any]:
    """Search by an uploaded ``image`` (with its ``mime`` type) or by an image ``url``."""
    if image is not None:
        cbir_id, public_url = _upload(image, mime or "application/octet-stream", timeout)
        query = {"rpt": "imageview", "cbir_id": cbir_id, "cbir_page": "sites"}
    else:
        public_url = url
        query = {"rpt": "imageview", "url": url, "cbir_page": "sites"}

    resp = network.get(SEARCH_URL, params=query, headers=_HEADERS, timeout=timeout, raise_for_httperror=False)
    if "showcaptcha" in str(resp.url):
        raise YandexError("asked for a CAPTCHA (too many searches from this connection)")
    if resp.status_code != 200:
        raise YandexError(f"answered HTTP {resp.status_code}")
    state = _initial_state(resp.text)
    result = _parse(state)
    result["public_url"] = public_url
    result["page_url"] = state.get("cbirPreview", {}).get("shareUrl") or f"{SEARCH_URL}?{urlencode(query)}"
    return result


def _upload(image: bytes, mime: str, timeout: float) -> tuple[str, str]:
    form = CurlMime()
    try:
        form.addpart(name="upfile", filename="image", content_type=mime, data=image)
        resp = network.post(
            SEARCH_URL,
            params={"rpt": "imageview", "format": "json", "request": _UPLOAD_REQUEST},
            multipart=form,
            headers=_HEADERS,
            timeout=timeout,
            raise_for_httperror=False,
        )
    finally:
        form.close()
    if "showcaptcha" in str(resp.url):
        raise YandexError("asked for a CAPTCHA (too many searches from this connection)")
    if resp.status_code != 200:
        raise YandexError(f"refused the upload (HTTP {resp.status_code})")
    try:
        params = resp.json()["blocks"][0]["params"]
        return params["cbirId"], params["originalImageUrl"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise YandexError("the upload answer has changed format") from exc


def _initial_state(page: str) -> dict[str, t.Any]:
    for raw in _STATE_RE.findall(page):
        if "initialState" not in raw:
            continue
        try:
            state = json.loads(html.unescape(raw))
        except ValueError:
            continue
        if isinstance(state, dict) and isinstance(state.get("initialState"), dict):
            return state["initialState"]
    raise YandexError("the results page has changed format")


def _absolute(url: str | None) -> str | None:
    if url and url.startswith("//"):
        return "https:" + url
    return url


def _parse(state: dict[str, t.Any]) -> dict[str, t.Any]:
    sites = []
    for site in state.get("cbirSites", {}).get("sites", []):
        if not site.get("url"):
            continue
        original = site.get("originalImage") or {}
        width, height = original.get("width"), original.get("height")
        sites.append(
            {
                "title": site.get("title") or site.get("domain") or site["url"],
                "url": site["url"],
                "domain": site.get("domain") or urlparse(site["url"]).netloc,
                "description": site.get("description") or "",
                "thumbnail": _absolute((site.get("thumb") or {}).get("url")),
                "image_url": original.get("url"),
                "size": f"{width}×{height}" if width and height else "",
            }
        )

    similar = []
    for thumb in state.get("cbirSimilar", {}).get("thumbs", []):
        link = parse_qs(urlparse(thumb.get("linkUrl", "")).query)
        image_url = (link.get("img_url") or [None])[0]
        if image_url and thumb.get("imageUrl"):
            similar.append({"thumbnail": _absolute(thumb["imageUrl"]), "url": image_url, "title": thumb.get("title") or ""})

    groups = state.get("cbirOtherSizes", {}).get("items", {})
    sizes = [
        {"label": item["text"], "url": item["url"]}
        for group in _SIZE_GROUPS
        for item in groups.get(group, [])
        if item.get("url") and item.get("text")
    ]

    tags = [tag["text"] for tag in state.get("cbirTags", {}).get("tags", []) if tag.get("text")]
    return {"sites": sites, "similar": similar, "sizes": sizes, "tags": tags}
