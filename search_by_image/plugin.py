"""SearXNG plugin: search by image.

Adds a page at ``/search-by-image`` where a photo can be uploaded, dropped,
pasted or given as a link, and a camera button next to the search box that
leads to it. The server asks Yandex (and trace.moe for anime screenshots) and
shows the results in SearXNG's own style. Google Lens, Bing, TinEye and
SauceNAO refuse requests from servers, so the page links to them with a public
copy of the image and the user's browser opens them.

Enable it in ``settings.yml``::

    plugins:
      search_by_image.plugin.SXNGPlugin:
        active: true
"""

from __future__ import annotations

import base64
import concurrent.futures
import ipaddress
import logging
import typing as t
from urllib.parse import quote, urlparse

import flask
from curl_cffi.requests.exceptions import RequestException, Timeout
from flask_babel import gettext

from searx.extended_types import sxng_request
from searx.plugins import Plugin, PluginInfo

from . import imagemeta, tracemoe, yandex

if t.TYPE_CHECKING:
    from searx.plugins import PluginCfg

PLUGIN_ID = "search_by_image"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
SOURCE_TIMEOUT = 20.0
# An upload is shown back to the user inline (as a data: URL) only when
# Yandex's copy is missing and the file is small enough to embed.
INLINE_PREVIEW_BYTES = 2 * 1024 * 1024
SITE_LIMIT = 30

log = logging.getLogger(__name__)

blueprint = flask.Blueprint(
    PLUGIN_ID,
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/search-by-image/static",
)


class InputError(ValueError):
    """The submitted image or link can't be searched."""


class SXNGPlugin(Plugin):
    """Reverse image search page plus a camera button in the search box."""

    id = PLUGIN_ID

    def __init__(self, plg_cfg: "PluginCfg") -> None:
        super().__init__(plg_cfg)
        self.info = PluginInfo(
            id=self.id,
            name=gettext("Search by image"),
            description=gettext(
                "Camera button next to the search box: find where a photo appears online and similar"
                " images (Yandex), or which anime scene a screenshot is from (trace.moe)"
            ),
            preference_section="ui",
        )

    def init(self, app: flask.Flask) -> bool:
        app.register_blueprint(blueprint)
        app.after_request(_add_camera_button)
        # SearXNG parses every request body before any view runs, so the
        # upload limit has to be the application's. Nothing else in SearXNG
        # accepts uploads; ordinary forms stay far below it.
        if app.config.get("MAX_CONTENT_LENGTH") is None:
            app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 1024
        return True


_camera_button_html: str | None = None


def _add_camera_button(response: flask.Response) -> flask.Response:
    """Put the camera button into the search box of the start and results pages."""
    global _camera_button_html  # pylint: disable=global-statement
    if (
        response.status_code != 200
        or response.mimetype != "text/html"
        or response.direct_passthrough
        or sxng_request.endpoint not in ("index", "search")
        or PLUGIN_ID not in getattr(sxng_request, "user_plugins", ())
    ):
        return response
    body = response.get_data(as_text=True)
    anchor = '<button id="send_search"'
    if anchor not in body:
        return response
    if _camera_button_html is None:
        _camera_button_html = flask.render_template_string(
            "{% from 'simple/icons.html' import icon_big %}"
            '<a id="sbi_camera" href="{{ href }}" title="{{ label }}" aria-label="{{ label }}">{{ icon_big("camera") }}</a>',
            href=flask.url_for(f"{PLUGIN_ID}.page"),
            label=gettext("Search by image"),
        )
    css = flask.url_for(f"{PLUGIN_ID}.static", filename="search_by_image.css")
    body = body.replace(anchor, _camera_button_html + anchor, 1)
    body = body.replace("</head>", f'<link rel="stylesheet" href="{css}" type="text/css">\n</head>', 1)
    response.set_data(body)
    return response


@blueprint.route("/search-by-image", methods=["GET", "POST"])
def page():
    # searx.webapp imports the plugins while it is being set up, so its
    # helpers can only be imported once a request arrives.
    from searx.webapp import image_proxify, render  # pylint: disable=import-outside-toplevel

    form = sxng_request.form  # POST fields merged with the query string
    upload = flask.request.files.get("image")
    has_upload = upload is not None and bool(upload.filename)
    url = "" if has_upload else (form.get("url") or "").strip()
    want_anime = form.get("anime") in ("1", "on", "true")
    context: dict[str, t.Any] = {
        "url_value": url,
        "anime": want_anime,
        "submitted": has_upload or bool(url),
        "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }
    if not context["submitted"]:
        return render("search_by_image.html", **context)

    try:
        if has_upload:
            raw = upload.read(MAX_UPLOAD_BYTES + 1)
            if not raw:
                raise InputError(gettext("The file is empty."))
            if len(raw) > MAX_UPLOAD_BYTES:
                raise InputError(gettext("The image is larger than %(mb)s MB.", mb=context["max_mb"]))
            image, mime = imagemeta.clean(raw)
            image_url = None
        else:
            image, mime = None, None
            image_url = _checked_url(url)
    except imagemeta.UnsupportedImage as exc:
        context["error"] = gettext("This file can't be searched: %(reason)s.", reason=str(exc))
        return render("search_by_image.html", **context), 400
    except InputError as exc:
        context["error"] = str(exc)
        return render("search_by_image.html", **context), 400

    hide_adult = int(sxng_request.preferences.get_value("safesearch") or 0) > 0
    found = _ask_sources(image, mime, image_url, want_anime, hide_adult)
    yandex_result = found.get("yandex")

    # Link the other search sites need: Yandex's public copy of an upload,
    # or the link the user gave.
    public_url = image_url or (yandex_result or {}).get("public_url")
    if public_url:
        preview = image_proxify(public_url)
    elif image is not None and len(image) <= INLINE_PREVIEW_BYTES:
        preview = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    else:
        preview = None

    context.update(
        {
            "preview": preview,
            "public_host": urlparse(public_url).netloc if public_url else "",
            "open_in": _open_in_links(public_url, yandex_result) if public_url else [],
            "yandex": yandex_result,
            "anime_result": found.get("anime"),
            "source_errors": found["errors"],
            "site_limit": SITE_LIMIT,
        }
    )
    return render("search_by_image.html", **context)


def _checked_url(url: str) -> str:
    if len(url) > 2048:
        raise InputError(gettext("The image link is too long."))
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise InputError(gettext("Enter a link that starts with http:// or https://."))
    host = parsed.hostname.lower()
    try:
        private = not ipaddress.ip_address(host).is_global
    except ValueError:
        private = host == "localhost" or host.endswith((".local", ".lan", ".home.arpa", ".internal"))
    if private:
        raise InputError(gettext("Search sites can't reach addresses inside a private network."))
    return url


def _ask_sources(
    image: bytes | None, mime: str | None, url: str | None, want_anime: bool, hide_adult: bool
) -> dict[str, t.Any]:
    jobs: dict[str, t.Callable[[], dict[str, t.Any]]] = {
        "yandex": lambda: yandex.search(image, mime, url, SOURCE_TIMEOUT),
    }
    if want_anime:
        jobs["anime"] = lambda: tracemoe.search(image, mime, url, SOURCE_TIMEOUT, hide_adult)
    names = {"yandex": "Yandex", "anime": "trace.moe"}

    found: dict[str, t.Any] = {"errors": []}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {key: pool.submit(job) for key, job in jobs.items()}
        for key, future in futures.items():
            try:
                found[key] = future.result()
            except (yandex.YandexError, tracemoe.TraceMoeError) as exc:
                found["errors"].append((names[key], str(exc)))
            except Timeout:
                found["errors"].append((names[key], gettext("no answer within %(s)s seconds", s=int(SOURCE_TIMEOUT))))
            except RequestException as exc:
                log.warning("search by image: %s unreachable: %s", names[key], exc)
                found["errors"].append((names[key], gettext("couldn't connect")))
            except Exception:  # pylint: disable=broad-except
                log.exception("search by image: %s failed", names[key])
                found["errors"].append((names[key], gettext("the request failed (details in the SearXNG log)")))
    return found


def _open_in_links(public_url: str, yandex_result: dict[str, t.Any] | None) -> list[tuple[str, str]]:
    q = quote(public_url, safe="")
    yandex_page = (yandex_result or {}).get("page_url") or f"{yandex.SEARCH_URL}?rpt=imageview&url={q}"
    return [
        ("Google Lens", f"https://lens.google.com/uploadbyurl?url={q}"),
        ("Bing", f"https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP&sbisrc=UrlPaste&q=imgurl:{q}"),
        ("TinEye", f"https://tineye.com/search?url={q}"),
        ("SauceNAO", f"https://saucenao.com/search.php?url={q}"),
        ("Yandex", yandex_page),
    ]
