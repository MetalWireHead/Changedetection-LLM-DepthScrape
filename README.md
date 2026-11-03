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

## Settings

### Global: Settings > Linked Content

Saved by changedetection.io in `<datastore>/linked_content.json`; they apply to every watch.

| Setting | |
|---|---|
| Look for new links in | **Text**: URLs written in the lines the change added. **HTML**: `href`, `data-href`, `data-url`, `formaction` and `onclick` targets in the fetched HTML, so links behind buttons are found. **Both**. |
| Scrape new links using | **Plain text**: changedetection.io's requests fetcher (no JavaScript). **Chrome**: the instance's browser fetcher (`WEBDRIVER_URL` / `PLAYWRIGHT_DRIVER_URL`), needed for JavaScript sites. |
| Same domain only | Only follow links on the watch's domain. |
| Max links, max characters per link, timeout | Limits. Max links counts every depth together. |
| Link depth | `0` = only the new links. `1` = also the links found inside those pages. `2` = one level further, and so on (0-5). Default for all watches. |
| Save the content of linked pages | Keeps a history of every linked page. The AI summary then only gets what changed in a page since its previous scrape. Default for all watches. |
| Send linked content to the AI | On/off for the AI change summary. Scraping, the tab and the history work either way. |
| Only send pages matching this regex to the AI | Optional. Empty = every linked page. |

### Per watch: the "Linked Content" tab of the watch's edit page

Saved with the watch's normal **Save** button, in `<datastore>/<watch uuid>/linked_content.json`.

| Setting | |
|---|---|
| Only follow links matching this regex | Empty = every new link (the same-domain rule still applies). |
| Never follow links matching this regex | Wins over the other two regexes. |
| Always scrape links matching this regex | Links on the page that match are scraped after every check, even when they are not new. Not limited by "only follow"; same-domain and "never follow" still apply. |
| Include filters / Remove elements | For the linked pages. One per line. Include: CSS selector, `xpath:`, `xpath1:`, `json:`, `jq:`, `jqraw:`. Remove: CSS selector or `xpath:`, applied first; links inside removed elements are not followed either. |
| Save a history / Link depth | Override the global defaults for this watch. |

#### Testing the settings: two buttons at the top of the tab

Both work on the settings as they are in the form right now, saved or not, and show their results in a panel to the right
of the form (the same layout as changedetection.io's own *Activate preview*).

- **Test link rules** fetches the watch's page again, the way a real check does (the watch's own fetcher, headers,
  proxy and browser steps), and lists every link on it, colour-coded: green *will be scraped*, blue *always scraped*,
  red *not scraped* with the reason (another domain, does not match "only follow", matches "never follow", over the
  maximum links...). Every link is treated as if it were new. With a link depth above 0 it also scrapes the pages
  that would be scraped (at most the maximum links) so you see the links one level further down, indented under the page
  they were found on.
- **Activate preview** lists the links that would be scraped and shows the page of the one you pick, after and before the
  include filters ("remove elements" is applied to both), using the scrape method from the global settings. It
  refreshes by itself when you edit the include filters or remove elements, and shows the error when a filter is invalid
  or matches nothing. Run *Test link rules* again after changing a regex or the depth.


## How it works

- A link is *new* when it is in the new HTML but not in the previous fetch's HTML (HTML), or in an added line (text).
  changedetection.io keeps the raw HTML of the newest two fetches only. Without the earlier HTML, HTML mode only
  counts links that are also named in an added line, so a whole page is never treated as new.
- After every detected change a background job scrapes the new links (and, after every check, the "always scrape"
  links), so the tab and the history are ready without anyone opening the page. The first check of a watch the add-on
  has not seen before only records its version.
- Every linked page is checked with changedetection.io's own fetch URL gate first, so links to private or internal
  addresses, `file://` and the like are refused (unless the instance allows them with `ALLOW_IANA_RESTRICTED_ADDRESSES`).
- Each link is fetched with changedetection.io's own fetcher classes. With a depth above 0, the links found in a
  scraped page (after the "remove elements" rules) are followed the same way, breadth first, within the max-links cap.
  Results are cached per watch in `<datastore>/<watch uuid>/linked-content/cache/`, one entry per change.
- With a history, every scraped page is saved under `<datastore>/<watch uuid>/linked-content/history/` whenever it
  differs from its last saved version. The AI gets the diff of each page (all of it the first time, nothing when
  unchanged) and the Linked Content tab shows what changed.
- **No hook exists for the summary prompt, the diff page tabs, or per-watch settings**, so the add-on wraps
  `changedetectionio.llm.evaluator.summarise_change` / `build_change_summary_prompt` once at runtime, adds both tabs with
  scripts injected through `get_html_head_extras`, registers a small `/linked-content/<uuid>` endpoint for the diff tab,
  and wraps the watch edit view to save the per-watch tab. These are the parts most likely to need an update when
  changedetection.io changes internally.

## Limits

- Not appended to the raw `{{diff}}` of a notification, only to the AI summary (and the tab).
- A summary that is already cached for a pair of versions is not regenerated when you change these settings.
- An old version of the page is never scraped "now" (that would show today's links, not the old ones): its Linked Content
  tab only shows what was saved when that change happened.
- A linked page that changes while the watched page does not is scraped (when it matches "always scrape") and saved, but
  nothing is sent to the AI, because the AI summary only runs for changes of the watched page.
- Every link scraped costs time and, with Chrome, memory: keep max links and depth low. The add-on is on for **every**
  watch (after each detected change it scrapes up to the maximum links). The only on/off switch is the global one under
  Settings > Linked Content; to keep a single watch out, give it a "never follow" regex of `.` (that matches every link).
- The AI "every change between versions" mode uses the newest change for finding links.
- Scraped page text is sent to your LLM provider, like the diff is.

## Tests

```bash
docker exec -w /app changedetection python -m pytest /addon/tests -q -p no:cacheprovider --rootdir=/addon
```
