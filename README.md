# AI Disclaimer:
This entire project was made by Sonnet 5.5 on high effort. Human-reviewed code and human testing will be added here once it is done.

# Linked Content - a changedetection.io add-on


Scrapes the **new links** that show up in a change and adds what it finds to

- the prompt of the **AI change summary** (the diff page's Summary and the notification `{{llm_summary}}`), and
- a new **Linked Content** tab on the diff page.

Built and tested against the `ghcr.io/dgtlmoon/changedetection.io` image, v0.60.10.

## Install

The add-on is a normal Python package. changedetection.io's Docker image installs anything listed in its
`EXTRA_PACKAGES` environment variable every time the container starts, so installing means: get this repo onto your
machine, mount it into the container, and point `EXTRA_PACKAGES` at the mounted folder.

### Option A - use this repo's `docker-compose.yml` (simplest)

It runs changedetection.io plus a Selenium Chrome browser (needed for the "Chrome" scrape option) with the add-on
already wired in.

1. Install Docker with Compose v2 (`docker compose version` should work).
2. Clone the repo and enter it:
   ```bash
   git clone https://github.com/MetalWireHead/Changedetection-LLM-DepthScrape.git
   cd Changedetection-LLM-DepthScrape
   ```
3. If the add-on is not on the default branch yet, switch to the branch that has it:
   ```bash
   git checkout claude/eager-planck-2cu9ux
   ```
4. Start it:
   ```bash
   docker compose up -d
   ```
   The compose file mounts the cloned folder (`./`) at `/addon` inside the container and sets
   `EXTRA_PACKAGES=--no-build-isolation -e /addon`, so the container installs the add-on from your clone on startup.
5. Check that it installed:
   ```bash
   docker compose logs changedetection | grep -i "linked"
   ```
   You should see `Successfully installed changedetection-linked-content`.
6. Open http://127.0.0.1:5000, go to **Settings > Linked Content** and choose your options. Save.

### Option B - add it to a compose file you already have

1. Clone the repo anywhere on the machine that runs Docker:
   ```bash
   git clone https://github.com/MetalWireHead/Changedetection-LLM-DepthScrape.git
   ```
2. In your compose file, on the changedetection.io service, add a volume pointing at your clone and the environment
   variable (the path on the left is where you cloned it, absolute or relative to the compose file):
   ```yaml
   services:
     changedetection:
       volumes:
         - changedetection-data:/datastore
         - ./Changedetection-LLM-DepthScrape:/addon      # your clone -> /addon in the container
       environment:
         - EXTRA_PACKAGES=--no-build-isolation -e /addon
   ```
   Mount it read-write (no `:ro`): the install writes package metadata into the folder.
3. For the "Chrome" scrape option the instance needs a browser: keep or enable the browser service from upstream's
   compose file and its `WEBDRIVER_URL` (or `PLAYWRIGHT_DRIVER_URL`) setting. Without one, only the plain-text option works.
4. Recreate the container so it picks up the changes:
   ```bash
   docker compose up -d
   ```
5. Check the logs and open **Settings > Linked Content**, as in Option A, steps 5 and 6.

### Updating and removing

- Update: `git pull` in your clone, then `docker compose restart changedetection`.
- Remove: delete the `/addon` volume line and the `EXTRA_PACKAGES` line, then `docker compose up -d`. Saved settings stay
  in the datastore as `linked_content.json` and are harmless.

### Without Docker

Run `pip install .` from the clone, in the same Python environment changedetection.io runs in, then restart it.

`docker-compose.yml` in this repo is upstream's, with the Selenium Chrome service and its `/dev/shm` volume switched on, and
upstream's `CHROME_OPTIONS` lines commented out because their indentation is invalid YAML.

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
