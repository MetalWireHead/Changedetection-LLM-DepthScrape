"""Glue: which links are new in a change, scraping them once (cached per watch), and rendering the result."""
import hashlib
import json
import os
import threading
import time

from loguru import logger
from markupsafe import Markup, escape

from . import links, scraper, settings as settings_module

# How many of the newest snapshots to compare a summary's text against to find which versions it describes
RECENT_SNAPSHOTS = 5
# changedetectionio.llm.summary_jobs runs the diff page's Summary on a pool with this thread name prefix
DIFF_PAGE_SUMMARY_THREAD_PREFIX = 'llm-summary'
# Bump when what gets scraped/stored changes shape, so older cache files are not reused
CACHE_VERSION = 2

_datastore = None
# uuid -> perform_site_check handler of the watch being processed right now (its fetcher holds the new HTML)
_live_handlers = {}
_key_locks = {}
_key_locks_guard = threading.Lock()


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


def remember_handler(watch, handler):
    _live_handlers[watch.get('uuid')] = handler


def forget_handler(watch):
    if watch:
        _live_handlers.pop(watch.get('uuid'), None)


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

    handler = _live_handlers.get(watch.get('uuid'))
    from_diff_page = threading.current_thread().name.startswith(DIFF_PAGE_SUMMARY_THREAD_PREFIX)

    if from_diff_page or handler is None:
        for index in range(len(dates) - 1, max(len(dates) - 1 - RECENT_SNAPSHOTS, 0), -1):
            if (watch.get_history_snapshot(timestamp=dates[index]) or '') == current_snapshot:
                return context_for_versions(watch, dates[index - 1], dates[index])

    live_html = getattr(getattr(handler, 'fetcher', None), 'content', None) if not from_diff_page else None
    return {
        'from_version': dates[-1],
        'to_version': None,
        'old_text': watch.get_history_snapshot(timestamp=dates[-1]) or '',
        'new_text': current_snapshot or '',
        'old_html': _fetched_html(watch, dates[-1]),
        'new_html': live_html if isinstance(live_html, str) else '',
    }


# --- scraping + cache ---------------------------------------------------------------------------

def _cache_path(watch, key):
    return os.path.join(watch.data_dir, 'linked-content', f'{key}.json')


def _cache_key(urls, cfg):
    signature = json.dumps([CACHE_VERSION, urls, cfg['scrape_method'], cfg['include_filter_list'], cfg['subtractive_selector_list'],
                            cfg['max_chars_per_link']], sort_keys=True)
    return hashlib.sha1(signature.encode()).hexdigest()[:20]


def _lock_for(key):
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def linked_content(watch, ctx, cfg, force=False):
    """{'links': [...], 'source': ..., 'method': ...} for the new links in `ctx`. Blocking, may scrape."""
    empty = {'links': [], 'source': cfg['link_source'], 'method': cfg['scrape_method'], 'found': 0}
    if not ctx:
        return empty

    urls = links.new_links(cfg['link_source'], watch.get('url', ''), ctx['old_text'], ctx['new_text'],
                           ctx['old_html'], ctx['new_html'])
    found = len(urls)
    urls = links.filter_links(urls, watch.get('url', ''), same_domain_only=cfg['same_domain_only'],
                              include_regex=cfg['link_include_regex'], exclude_regex=cfg['link_exclude_regex'],
                              limit=cfg['max_links'])
    if not urls:
        return {**empty, 'found': found}

    key = _cache_key(urls, cfg)
    path = _cache_path(watch, key)
    with _lock_for(key):
        if not force and os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"linked_content: ignoring unreadable cache {path}: {e}")

        logger.info(f"linked_content: scraping {len(urls)} new link(s) for {watch.get('uuid')} via {cfg['scrape_method']}")
        payload = {**empty, 'found': found, 'generated_at': int(time.time()),
                   'links': [scraper.scrape(url, cfg) for url in urls]}
        # Don't cache a run where nothing worked, so the next look retries
        if any(not item['error'] for item in payload['links']):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
        return payload


# --- output -------------------------------------------------------------------------------------

def prompt_block(payload):
    """Text appended to the LLM prompt, '' when there is nothing to add."""
    good = [item for item in payload['links'] if item['text']]
    if not good:
        return ''
    parts = ['\nContent scraped from the links that are new in this change. This is supplementary context: '
             'the +/- diff above remains the record of what changed on the page itself, and these pages '
             'are new content that the change links to.']
    for item in good:
        parts.append(f"\n--- Linked page: {item['url']}" + (' (truncated)' if item['truncated'] else '') + f"\n{item['text']}")
    return '\n'.join(parts)


def tab_html(payload, cfg):
    """HTML fragment for the 'Linked Content' tab. Everything scraped is escaped."""
    if not cfg['enabled']:
        return Markup('<p>Linked content scraping is switched off in Settings &gt; Linked Content.</p>')
    method = 'Chrome' if payload['method'] == 'webdriver' else 'plain text'
    if not payload['links']:
        note = ''
        if payload.get('found'):
            note = (f" {payload['found']} link(s) were found but removed by the same-domain / include / exclude "
                    f"rules in Settings &gt; Linked Content.")
        return Markup(f"<p>No new links were found in this change (looked in: {escape(payload['source'])}).{note}</p>")

    out = [Markup(f"<p>{len(payload['links'])} new link(s), scraped with {method}.</p>")]
    for item in payload['links']:
        status = f" &middot; HTTP {escape(item['status_code'])}" if item.get('status_code') else ''
        error = f'<p class="error">{escape(item["error"])}</p>' if item['error'] else ''
        trunc = ' <em>(truncated)</em>' if item['truncated'] else ''
        out.append(Markup(
            f'<details open class="linked-content-item"><summary><a href="{escape(item["url"])}" target="_blank" '
            f'rel="noopener noreferrer">{escape(item["url"])}</a>{status}{trunc}</summary>{error}'
            f'<pre class="linked-content-text">{escape(item["text"])}</pre></details>'))
    return Markup('').join(out)
