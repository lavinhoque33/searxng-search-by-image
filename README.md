# Search by image for SearXNG

A [SearXNG](https://github.com/searxng/searxng) plugin that adds reverse image search: find where a photo appears online,
other sizes of it, similar pictures, and which anime scene a screenshot is from.

- A camera button next to the search box opens `/search-by-image`.
- There you can choose, drop or paste (Ctrl+V) a picture, or give a link to one.
- Results appear in SearXNG's own style, and thumbnails load through SearXNG's image proxy.
- Works with the stock `searxng/searxng` image; no fork, no extra container.

## Where the results come from

| Source | Asked by | What it gives |
|---|---|---|
| [Yandex Images](https://yandex.com/images/) | your SearXNG server | pages that contain the image, other sizes, similar images, "looks like" keywords |
| [trace.moe](https://trace.moe) (only when the anime box is ticked) | your SearXNG server | anime title, episode and time of the scene, linked to AniList; matches under 90 % are hidden |
| Google Lens, Bing, TinEye, SauceNAO, Yandex | your browser | "Also search on" buttons that open those sites with the image |

Google Lens, Bing, TinEye and SauceNAO block requests from servers, so SearXNG cannot fetch their results. The
buttons open them in your browser instead, and each site then downloads the image from a public link:

- for an uploaded picture, that link is the copy Yandex keeps after the upload;
- for a picture given by link, it is that link.

If Yandex fails, an uploaded picture has no public link and the buttons are not shown.

## Privacy

- The server removes location, camera details, comments and edit history from uploads before sending them on. It keeps
  only the colour profile and the orientation flag. This is done by rewriting the file's metadata blocks, without
  re-encoding the picture.
- In the browser, photos larger than 1600 px or 4 MB, and formats other than JPEG, PNG, WebP and GIF (such as HEIC),
  are first redrawn as a JPEG of at most 1600 px. That also drops their metadata.
- Uploads go to Yandex; with the anime box ticked, they also go to trace.moe. Yandex keeps a copy at a public link
  that only it and the person searching know.
- The "Also search on" buttons hand that link to whichever site you click.
- Nothing is stored on the SearXNG server.

## Install

The plugin is the `search_by_image` folder. SearXNG's container imports plugins from `/usr/local/searxng`, so mount the
folder there. For example, with the official compose setup:

```sh
git clone https://github.com/lavinhoque33/searxng-search-by-image.git
```

```yaml
services:
  searxng:
    image: docker.io/searxng/searxng:latest
    volumes:
      - ./config:/etc/searxng
      - ./searxng-search-by-image/search_by_image:/usr/local/searxng/search_by_image:ro
```

Then enable it in `settings.yml`.

> [!IMPORTANT]
> A `plugins:` section replaces SearXNG's default plugin list instead of adding to it, so repeat the defaults. The
> list below matches SearXNG 2026.11; compare it with the `plugins:` section of
> [searx/settings.yml](https://github.com/searxng/searxng/blob/master/searx/settings.yml) of your version.

```yaml
plugins:
  searx.plugins.calculator.SXNGPlugin:
    active: true
  searx.plugins.infinite_scroll.SXNGPlugin:
    active: false
  searx.plugins.hash_plugin.SXNGPlugin:
    active: true
  searx.plugins.self_info.SXNGPlugin:
    active: true
  searx.plugins.unit_converter.SXNGPlugin:
    active: true
  searx.plugins.ahmia_filter.SXNGPlugin:
    active: true
  searx.plugins.hostnames.SXNGPlugin:
    active: true
  searx.plugins.time_zone.SXNGPlugin:
    active: true
  searx.plugins.oa_doi_rewrite.SXNGPlugin:
    active: false
  searx.plugins.tor_check.SXNGPlugin:
    active: false
  searx.plugins.tracker_url_remover.SXNGPlugin:
    active: true
  search_by_image.plugin.SXNGPlugin:
    active: true
```

`server.image_proxy: true` is recommended; without it thumbnails load straight from Yandex. Restart the container.
Users can switch the camera button off under Preferences → User interface.

Without Docker, put `search_by_image` anywhere on SearXNG's Python path (for example next to the `searx` package) and
use the same settings.

## Limits

- **No official Yandex API.** The plugin reads Yandex's own search page, which can change without notice; the page then
  reports that Yandex's results page has changed format. Heavy use from one IP address can make Yandex ask for a
  CAPTCHA, which the page reports as well.
- **trace.moe quota.** Without an API key trace.moe allows 100 searches per IP address per period and one search at a
  time. The page shows how many are left. trace.moe also can't download images from some sites (Wikimedia, for one).
- **Google Lens and SauceNAO may challenge you.** Google can show its "unusual traffic" CAPTCHA, and SauceNAO a
  Cloudflare check; both are passed by clicking through.
- **Upload limit.** Uploads are limited to 15 MB. The plugin sets Flask's request size limit to 16 MB unless your setup
  already sets one.
- **Camera button placement.** SearXNG has no hook for adding buttons, so the plugin inserts it into the start and
  results pages right before the search button (`<button id="send_search"`). If a theme change removes that element,
  the button disappears; `/search-by-image` still works.
- Only the `simple` theme is supported.

## Development

```sh
python3 -m unittest discover tests   # metadata removal; needs no SearXNG
```

To try the whole plugin, run the official image with the folder mounted as above and a test `settings.yml`.

## License

[AGPL-3.0-or-later](LICENSE), like SearXNG, whose code the plugin runs inside.
