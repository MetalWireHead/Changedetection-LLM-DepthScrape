# Linked Content - a changedetection.io add-on

Scrapes the **new links** that show up in a change and adds what it finds to

- the prompt of the **AI change summary** (the diff page's Summary and the notification `{{llm_summary}}`), and
- a new **Linked Content** tab on the diff page.

Built and tested against the `ghcr.io/dgtlmoon/changedetection.io` image, v0.60.10.

## Install

Docker: the add-on is a normal pip package, changedetection.io installs it from `EXTRA_PACKAGES` on every start.
`docker-compose.yml` in this repo mounts the folder at `/addon` and sets

```yaml
environment:
  - EXTRA_PACKAGES=--no-build-isolation -e /addon
```

Without Docker: `pip install .` into the environment changedetection.io runs in, then restart it.

`docker-compose.yml` is upstream's, with the Selenium Chrome service and its `/dev/shm` volume switched on (needed for the
"Chrome" scrape option), and upstream's `CHROME_OPTIONS` lines commented out because their indentation is invalid YAML.

## Settings (Settings > Linked Content)

| Setting | |
|---|---|
| Look for new links in | **Text**: URLs written in the lines the change added. **HTML**: `href`, `data-href`, `data-url`, `formaction` and `onclick` targets in the fetched HTML, so links behind buttons are found. **Both**. |
| Scrape new links using | **Plain text**: changedetection.io's requests fetcher (no JavaScript). **Chrome**: the instance's browser fetcher (`WEBDRIVER_URL` / `PLAYWRIGHT_DRIVER_URL`), needed for JavaScript sites. |
| Include filters | One per line, same syntax as a watch: CSS selector, `xpath:`, `xpath1:`, `json:`, `jq:`, `jqraw:`. |
| Remove elements | One per line: CSS selector or `xpath:`. Applied before the include filters, like the watch setting. |
| Same domain / include regex / exclude regex | Which of the new links to follow. |
| Max links, max characters per link, timeout | Limits. |

Settings are saved by changedetection.io in `<datastore>/linked_content.json`. They apply to every watch.

## How it works

- A link is *new* when it is in the new HTML but not in the previous fetch's HTML (HTML), or in an added line (text).
  changedetection.io keeps the raw HTML of the newest two fetches only. Without the earlier HTML, HTML mode only
  counts links that are also named in an added line, so a whole page is never treated as new.
- Each link is fetched with changedetection.io's own fetcher classes. The result is cached per watch in
  `<datastore>/<watch uuid>/linked-content/`, so the tab and the summary never scrape the same links twice.
- **No hook exists for the summary prompt or the diff page tabs**, so the add-on wraps
  `changedetectionio.llm.evaluator.summarise_change` / `build_change_summary_prompt` once at runtime, and the tab is added
  by a script injected through `get_html_head_extras` that reads a small `/linked-content/<uuid>` endpoint the add-on
  registers. These are the parts most likely to need an update when changedetection.io changes internally.

## Limits

- Not appended to the raw `{{diff}}` of a notification, only to the AI summary (and the tab).
- A summary that is already cached for a pair of versions is not regenerated when you change these settings.
- The AI "every change between versions" mode uses the newest change for finding links.
- Scraped page text is sent to your LLM provider, like the diff is.

## Tests

```bash
docker exec -w /app changedetection python -m pytest /addon/tests -q -p no:cacheprovider --rootdir=/addon
```
