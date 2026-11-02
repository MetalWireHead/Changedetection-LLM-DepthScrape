"""Glue: which links to scrape for a change, scraping them (cached, optionally with a history), and rendering the result."""
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from loguru import logger
from markupsafe import Markup, escape

from . import history, links, scraper, settings as settings_module

# How many of the newest snapshots to compare a summary's text against to find which versions it describes
RECENT_SNAPSHOTS = 5
# changedetectionio.llm.summary_jobs runs the diff page's Summary on a pool with this thread name prefix
DIFF_PAGE_SUMMARY_THREAD_PREFIX = 'llm-summary'
# Bump when what gets scraped/stored changes shape, so older cache files are not reused
CACHE_VERSION = 3
# A scrape younger than this is reused instead of repeated (the summary and the after-check job share one)
FRESH_SECONDS = 120
STATE_FILE = 'linked_content_state.json'

_datastore = None
# uuid -> {'handler': perform_site_check handler, 'html': the fetched HTML} of the watch being processed right now
_live = {}
_key_locks = {}
_key_locks_guard = threading.Lock()
_background = ThreadPoolExecutor(max_workers=2, thread_name_prefix='linked-content-bg')
_background_running = set()
_background_guard = threading.Lock()


def set_datastore(datastore):
    global _datastore
    if datastore is not None:
        _datastore = datastore


def get_datastore():
    if _datastore is not None:
        return _datastore
    try:
        from changedetectionio import flask_app
        return flask_app.datastore
    except Exception:
        return None


def load_settings(datastore=None):
    datastore = datastore or get_datastore()
    return settings_module.load(datastore.datastore_path) if datastore else settings_module.clean({})


def settings_for(watch, datastore=None):
    """Global settings with this watch's own settings on top."""
    return settings_module.effective(load_settings(datastore), settings_module.load_watch(watch))


def remember_handler(watch, handler):
    """Track the handler processing `watch`, and keep the HTML it fetched: the worker clears it before update_finalize."""
    entry = {'handler': handler, 'html': None}
    _live[watch.get('uuid')] = entry

    original = getattr(handler, 'call_browser', None)
    if original is not None:
        async def call_browser(*args, **kwargs):
            result = await original(*args, **kwargs)
            content = getattr(getattr(handler, 'fetcher', None), 'content', None)
            entry['html'] = content if isinstance(content, str) else None
            return result

        handler.call_browser = call_browser


def forget_handler(watch):
    """Stop tracking the handler of `watch`, returns what was tracked (or None)."""
    return _live.pop(watch.get('uuid'), None) if watch else None


# --- which snapshots/HTML a change covers -------------------------------------------------------

def _fetched_html(watch, timestamp):
    try:
        return watch.get_fetched_html(timestamp) or ''
    except Exception:
        return ''


def context_for_versions(watch, from_version, to_version):
    """The two versions the diff page is showing."""
    return {
        'from_version': from_version,
        'to_version': to_version,
        'old_text': watch.get_history_snapshot(timestamp=from_version) or '',
        'new_text': watch.get_history_snapshot(timestamp=to_version) or '',
        'old_html': _fetched_html(watch, from_version),
        'new_html': _fetched_html(watch, to_version),
        'changed': True,
    }


def context_for_summary(watch, current_snapshot):
    """What summarise_change() is describing, from the text it was handed.

    From the worker, `current_snapshot` is the content that is about to be saved: the previous version is the
    newest one in history and the new HTML is still on the handler that is processing the watch. From the
    diff page's Summary job (a thread named 'llm-summary...') it is a stored snapshot, so the version pair is
    looked up by matching the text. The thread is what tells them apart: a page that flips back to an older
    state makes its new content equal an old snapshot, so the content alone cannot.
    """
    dates = list(watch.history.keys())
    if not dates:
        return None

    entry = _live.get(watch.get('uuid'))
    from_diff_page = threading.current_thread().name.startswith(DIFF_PAGE_SUMMARY_THREAD_PREFIX)

    if from_diff_page or entry is None:
        for index in range(len(dates) - 1, max(len(dates) - 1 - RECENT_SNAPSHOTS, 0), -1):
            if (watch.get_history_snapshot(timestamp=dates[index]) or '') == current_snapshot:
                return context_for_versions(watch, dates[index - 1], dates[index])

    live_html = entry['html'] if entry and not from_diff_page else None
    return {
        'from_version': dates[-1],
        'to_version': None,
        'old_text': watch.get_history_snapshot(timestamp=dates[-1]) or '',
        'new_text': current_snapshot or '',
        'old_html': _fetched_html(watch, dates[-1]),
        'new_html': live_html if isinstance(live_html, str) else '',
        'changed': True,
    }


def may_scrape_now(watch, ctx):
    """Only the newest state of the page can be scraped now: scraping today's content for an old version would be wrong."""
    dates = list(watch.history.keys())
    return ctx.get('to_version') is None or (bool(dates) and str(ctx['to_version']) == str(dates[-1]))


# --- choosing and scraping links ----------------------------------------------------------------

def seeds_for(watch, ctx, cfg):
    """([(url, 'new'|'always')], number of new links found before filtering) for a change."""
    base = watch.get('url', '')
    same_domain = cfg['same_domain_only']

    found_new = []
    if ctx.get('changed', True):
        found_new = links.new_links(cfg['link_source'], base, ctx['old_text'], ctx['new_text'],
                                    ctx['old_html'], ctx['new_html'])
    new = links.filter_links(found_new, base, same_domain_only=same_domain, include_regex=cfg['link_include_regex'],
                             exclude_regex=cfg['link_exclude_regex'], limit=cfg['max_links'])

    always = []
    always_regex = links.compile_regex(cfg['always_scrape_regex'])
    if always_regex:
        # The always-scrape regex is its own selector, so the 'only follow' regex is not applied to it
        matching = [u for u in links.current_links(cfg['link_source'], base, ctx['new_text'], ctx['new_html'])
                    if always_regex.search(u)]
        always = [u for u in links.filter_links(matching, base, same_domain_only=same_domain,
                                                exclude_regex=cfg['link_exclude_regex'], limit=cfg['max_links'])
                  if u not in new]

    return ([(u, 'new') for u in new] + [(u, 'always') for u in always])[:cfg['max_links']], len(found_new)


def crawl(watch, seeds, cfg):
    """Scrape the seeds, then the links found in the scraped pages down to cfg['link_depth'], breadth first.

    `max_links` caps all depths together. Deeper links follow the same rules as new ones: same domain, 'only follow'
    and 'never follow' regexes.
    """
    base = watch.get('url', '')
    seen = {url for url, _ in seeds}
    queue = [(url, 0, None, kind) for url, kind in seeds]
    results = []
    while queue and len(results) < cfg['max_links']:
        url, depth, parent, kind = queue.pop(0)
        deeper = depth < cfg['link_depth']
        result = scraper.scrape(url, cfg, want_links=deeper)
        found_links = result.pop('links', [])
        result.update(depth=depth, parent=parent, kind=kind)
        results.append(result)
        if deeper and not result['error']:
            for child in links.filter_links(found_links, base, same_domain_only=cfg['same_domain_only'],
                                            include_regex=cfg['link_include_regex'],
                                            exclude_regex=cfg['link_exclude_regex'], limit=cfg['max_links']):
                if child not in seen:
                    seen.add(child)
                    queue.append((child, depth + 1, url, 'linked'))
    return results


def _cache_path(watch, key):
    return os.path.join(watch.data_dir, 'linked-content', 'cache', f'{key}.json')


def _cache_key(seeds, cfg, ctx):
    """One entry per change: the version it starts from is in the key, so a page that flips back to an earlier state
    gets a fresh scrape. The summary (before the new version is saved) and the after-check job (after) agree on it."""
    signature = json.dumps([
        CACHE_VERSION, seeds, cfg['scrape_method'], cfg['include_filter_list'], cfg['subtractive_selector_list'],
        cfg['max_chars_per_link'], cfg['max_links'], cfg['link_depth'], cfg['save_history'], cfg['link_include_regex'],
        cfg['link_exclude_regex'], cfg['same_domain_only'], str(ctx.get('from_version')),
        hashlib.md5((ctx['new_text'] or '').encode()).hexdigest(),
    ], sort_keys=True)
    return hashlib.sha1(signature.encode()).hexdigest()[:20]


def _lock_for(key):
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _read_cache(path, max_age):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        logger.warning(f"linked_content: ignoring unreadable cache {path}: {e}")
        return None
    if max_age is not None and time.time() - payload.get('generated_at', 0) > max_age:
        return None
    return payload


def linked_content(watch, ctx, cfg, allow_scrape=True, max_age=None):
    """The scraped linked pages for the change in `ctx`, or None when they are not saved and may not be scraped now.

    Blocking, may scrape. `max_age` (seconds) makes a saved result older than that count as missing.
    """
    empty = {'links': [], 'source': cfg['link_source'], 'method': cfg['scrape_method'], 'found': 0,
             'history': cfg['save_history']}
    if not ctx:
        return empty

    seeds, found = seeds_for(watch, ctx, cfg)
    if not seeds:
        return {**empty, 'found': found}

    key = _cache_key(seeds, cfg, ctx)
    path = _cache_path(watch, key)
    with _lock_for(key):
        cached = _read_cache(path, max_age)
        if cached is not None:
            return cached
        if not allow_scrape:
            return None

        logger.info(f"linked_content: scraping {len(seeds)} link(s) for {watch.get('uuid')} via {cfg['scrape_method']}"
                    f" (depth {cfg['link_depth']})")
        results = crawl(watch, seeds, cfg)
        if cfg['save_history']:
            for item in results:
                if item['text'] and not item['error']:
                    item['history'] = history.record(watch, item['url'], item['text'])
        payload = {**empty, 'found': found, 'generated_at': int(time.time()), 'links': results}
        # Don't keep a run where nothing worked, so the next look retries
        if any(not item['error'] for item in results):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
        return payload


def payload_for_versions(watch, datastore, from_version, to_version):
    """(payload or None, cfg) for the two versions the diff page shows."""
    cfg = settings_for(watch, datastore)
    if not cfg['enabled']:
        return None, cfg
    ctx = context_for_versions(watch, from_version, to_version)
    return linked_content(watch, ctx, cfg, allow_scrape=may_scrape_now(watch, ctx)), cfg


# --- after every check --------------------------------------------------------------------------

def _state_path(watch):
    return os.path.join(watch.data_dir, STATE_FILE)


def _load_state(watch):
    try:
        with open(_state_path(watch), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(watch, state):
    os.makedirs(watch.data_dir, exist_ok=True)
    with open(_state_path(watch), 'w', encoding='utf-8') as f:
        json.dump(state, f)


def after_check(watch, datastore, entry):
    """Called when the worker is done with a watch. Scrapes in the background so the worker is never held up."""
    uuid = watch.get('uuid')
    live_html = (entry or {}).get('html')
    with _background_guard:
        if uuid in _background_running:
            return
        _background_running.add(uuid)

    def job():
        try:
            _after_check_job(watch, datastore, live_html)
        except Exception:
            logger.exception(f"linked_content: background scrape failed for {uuid}")
        finally:
            with _background_guard:
                _background_running.discard(uuid)

    _background.submit(job)


def _after_check_job(watch, datastore, live_html):
    """After a detected change: scrape its new links (so the tab and the history are ready). After every check:
    scrape the 'always scrape' links. The first check of a watch the add-on has not seen only records its version."""
    cfg = settings_for(watch, datastore)
    dates = list(watch.history.keys())
    if not cfg['enabled'] or not dates:
        return

    newest = str(dates[-1])
    state = _load_state(watch)
    changed = 'last_version' in state and state['last_version'] != newest and len(dates) >= 2
    if state.get('last_version') != newest:
        _save_state(watch, {**state, 'last_version': newest})

    if changed:
        ctx = context_for_versions(watch, dates[-2], dates[-1])
        ctx['new_html'] = ctx['new_html'] or (live_html or '')
    elif cfg['always_scrape_regex']:
        text = watch.get_history_snapshot(timestamp=dates[-1]) or ''
        ctx = {'from_version': dates[-1], 'to_version': dates[-1], 'old_text': text, 'new_text': text, 'old_html': '',
               'new_html': live_html or _fetched_html(watch, dates[-1]), 'changed': False}
    else:
        return

    linked_content(watch, ctx, cfg, allow_scrape=True, max_age=FRESH_SECONDS)


# --- output -------------------------------------------------------------------------------------

def _when(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def prompt_block(payload, cfg):
    """Text appended to the LLM prompt, '' when there is nothing to add.

    Only pages whose URL matches the LLM link regex (when set) are sent. With a history, a page is sent as what
    changed since its previous saved version (all of it the first time, nothing when unchanged).
    """
    if not payload or not cfg['send_to_llm']:
        return ''
    only = links.compile_regex(cfg['llm_link_regex'])
    parts = []
    for item in payload['links']:
        if not item['text'] or (only and not only.search(item['url'])):
            continue
        state = item.get('history')
        if payload.get('history') and state:
            if state['state'] == 'unchanged':
                continue
            if state['state'] == 'changed':
                body = ('Changes since the previous scrape of this page '
                        f"({_when(state['previous_at'])}), + added and - removed:\n{state['diff']}")
            else:
                body = item['text']
        else:
            body = item['text']
        origin = f" (found on {item['parent']})" if item.get('parent') else ''
        parts.append(f"\n--- Linked page: {item['url']}{origin}" + (' (truncated)' if item['truncated'] else '') + f"\n{body}")
    if not parts:
        return ''
    intro = ('\nContent scraped from the links of this change. This is supplementary context: the +/- diff above remains '
             'the record of what changed on the page itself, and these pages are what the change links to.')
    if payload.get('history'):
        intro += ' Linked pages that did not change since their previous scrape are left out.'
    return '\n'.join([intro] + parts)


def _diff_html(diff):
    """One block-level span per line (so no newlines between them), empty lines kept visible."""
    lines = []
    for line in diff.splitlines():
        css = 'linked-content-added' if line.startswith('+') else 'linked-content-removed' if line.startswith('-') else ''
        lines.append(f'<span class="{css}">{escape(line) or Markup("&nbsp;")}</span>')
    return Markup(''.join(lines))


def tab_html(payload, cfg):
    """HTML fragment for the 'Linked Content' tab. Everything scraped is escaped."""
    if not cfg['enabled']:
        return Markup('<p>Linked content scraping is switched off in Settings &gt; Linked Content.</p>')
    if payload is None:
        return Markup('<p>No linked content was saved for this version of the page. Only the newest change is '
                      'scraped on demand, because scraping today would not show what the links held back then.</p>')
    method = 'Chrome' if payload['method'] == 'webdriver' else 'plain text'
    if not payload['links']:
        note = ''
        if payload.get('found'):
            note = (f" {payload['found']} link(s) were found but removed by the same-domain / only follow / never follow "
                    f"rules.")
        return Markup(f"<p>No new links were found in this change (looked in: {escape(payload['source'])}).{note}</p>")

    out = [Markup(f"<p>{len(payload['links'])} link(s), scraped with {method}.</p>")]
    for item in payload['links']:
        labels = []
        if item.get('depth'):
            labels.append(f"depth {item['depth']}, found on {escape(item.get('parent') or '')}")
        elif item.get('kind') == 'always':
            labels.append('always scraped')
        else:
            labels.append('new link')
        if item.get('status_code'):
            labels.append(f"HTTP {escape(item['status_code'])}")
        if item['truncated']:
            labels.append('truncated')
        state = item.get('history')
        diff_block = ''
        if state:
            if state['state'] == 'changed':
                labels.append(f"changed since {_when(state['previous_at'])}")
                diff_block = (f'<details open><summary>What changed</summary><pre class="linked-content-diff">'
                              f'{_diff_html(state["diff"])}</pre></details>')
            elif state['state'] == 'unchanged':
                labels.append(f"unchanged since {_when(state['previous_at'])}")
            else:
                labels.append('first version saved')
        error = f'<p class="error">{escape(item["error"])}</p>' if item['error'] else ''
        out.append(Markup(
            f'<details open class="linked-content-item" style="margin-left:{int(item.get("depth") or 0) * 1.5}em">'
            f'<summary><a href="{escape(item["url"])}" target="_blank" rel="noopener noreferrer">{escape(item["url"])}</a>'
            f' &middot; {" &middot; ".join(str(l) for l in labels)}</summary>{error}{diff_block}'
            f'<pre class="linked-content-text">{escape(item["text"])}</pre></details>'))
    return Markup('').join(out)
