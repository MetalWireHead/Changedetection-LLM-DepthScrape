"""Settings.

Global ones: Settings > Linked Content, stored by changedetection.io in <datastore>/linked_content.json.
Per watch ones: the Linked Content tab of the watch edit page, stored in <watch data dir>/linked_content.json.
`effective()` merges the two into the one dict the rest of the add-on works with.
"""
import json
import os
import re

from wtforms import BooleanField, Form, IntegerField, SelectField, StringField

PLUGIN_ID = 'linked_content'
WATCH_FILE = 'linked_content.json'
MAX_LINK_DEPTH = 5

LINK_SOURCES = [
    ('both', 'Text and HTML'),
    ('text', 'Text only - URLs written out in the new text of the diff'),
    ('html', 'HTML only - href / data-href / formaction / onclick in the fetched HTML (finds links inside buttons)'),
]
SCRAPE_METHODS = [
    ('requests', "Plain text - changedetection.io's own requests fetcher (no JavaScript)"),
    ('webdriver', "Chrome - changedetection.io's WebDriver/Playwright browser (runs JavaScript)"),
]

DEFAULTS = {
    'enabled': True,
    'link_source': 'both',
    'scrape_method': 'requests',
    'same_domain_only': True,
    'max_links': 10,
    'max_chars_per_link': 5000,
    'timeout': 30,
    'send_to_llm': True,
    'llm_link_regex': '',
    'save_history': False,
    'link_depth': 0,
}

WATCH_DEFAULTS = {
    'link_include_regex': '',
    'link_exclude_regex': '',
    'always_scrape_regex': '',
    'include_filters': '',
    'subtractive_selectors': '',
    'save_history': '',  # '' = use the global setting, 'yes', 'no'
    'link_depth': '',    # '' = use the global setting
}


class LinkedContentForm(Form):
    enabled = BooleanField('Scrape new links found in a diff', default=DEFAULTS['enabled'])
    link_source = SelectField('Look for new links in', choices=LINK_SOURCES, default=DEFAULTS['link_source'])
    scrape_method = SelectField('Scrape new links using', choices=SCRAPE_METHODS, default=DEFAULTS['scrape_method'])
    same_domain_only = BooleanField('Only follow links on the same domain as the watch', default=DEFAULTS['same_domain_only'])
    max_links = IntegerField('Maximum links to scrape per change (all depths together)', default=DEFAULTS['max_links'])
    max_chars_per_link = IntegerField('Maximum characters kept per link', default=DEFAULTS['max_chars_per_link'])
    timeout = IntegerField('Timeout per link (seconds)', default=DEFAULTS['timeout'])
    link_depth = IntegerField(
        f'Link depth: 0 = only the new links, 1 = also the links found in those pages, and so on (0-{MAX_LINK_DEPTH}). '
        'Can be changed per watch', default=DEFAULTS['link_depth'])
    save_history = BooleanField(
        'Save the content of linked pages so there is a history; the AI summary then only gets what changed. '
        'Can be changed per watch', default=DEFAULTS['save_history'])
    send_to_llm = BooleanField('Send linked content to the AI for the change summary', default=DEFAULTS['send_to_llm'])
    llm_link_regex = StringField('Only send linked pages whose URL matches this regex to the AI (optional, empty = all)',
                                 default='')


def _as_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _as_lines(value):
    return [line.strip() for line in (value or '').splitlines() if line.strip()]


def clean(raw):
    """Saved global settings dict -> settings with every key present and sane."""
    raw = raw or {}
    s = dict(DEFAULTS)
    s.update({k: raw[k] for k in DEFAULTS if k in raw and raw[k] is not None})
    for flag in ('enabled', 'same_domain_only', 'send_to_llm', 'save_history'):
        s[flag] = bool(s[flag])
    if s['link_source'] not in dict(LINK_SOURCES):
        s['link_source'] = DEFAULTS['link_source']
    if s['scrape_method'] not in dict(SCRAPE_METHODS):
        s['scrape_method'] = DEFAULTS['scrape_method']
    s['max_links'] = _as_int(s['max_links'], DEFAULTS['max_links'], 1, 50)
    s['max_chars_per_link'] = _as_int(s['max_chars_per_link'], DEFAULTS['max_chars_per_link'], 100, 200_000)
    s['timeout'] = _as_int(s['timeout'], DEFAULTS['timeout'], 3, 300)
    s['link_depth'] = _as_int(s['link_depth'], DEFAULTS['link_depth'], 0, MAX_LINK_DEPTH)
    s['llm_link_regex'] = (s['llm_link_regex'] or '').strip()
    return s


def clean_watch(raw):
    """Saved per-watch settings -> every key present, text trimmed, overrides validated."""
    raw = raw or {}
    s = dict(WATCH_DEFAULTS)
    s.update({k: raw[k] for k in WATCH_DEFAULTS if k in raw and raw[k] is not None})
    for key in ('link_include_regex', 'link_exclude_regex', 'always_scrape_regex'):
        s[key] = str(s[key]).strip()
    for key in ('include_filters', 'subtractive_selectors'):
        s[key] = '\n'.join(_as_lines(str(s[key])))
    s['save_history'] = s['save_history'] if s['save_history'] in ('yes', 'no') else ''
    depth = str(s['link_depth']).strip()
    s['link_depth'] = str(_as_int(depth, '', 0, MAX_LINK_DEPTH)) if depth else ''
    return s


def invalid_regexes(watch_settings):
    """Names of the per-watch regex fields that do not compile."""
    bad = []
    for key in ('link_include_regex', 'link_exclude_regex', 'always_scrape_regex'):
        try:
            re.compile(watch_settings[key])
        except re.error:
            bad.append(key)
    return bad


def effective(global_settings, watch_settings):
    """The one settings dict the add-on works with for a watch: global values, per-watch values, resolved overrides."""
    cfg = dict(global_settings)
    watch_settings = clean_watch(watch_settings)
    cfg.update({k: watch_settings[k] for k in ('link_include_regex', 'link_exclude_regex', 'always_scrape_regex')})
    if watch_settings['save_history']:
        cfg['save_history'] = watch_settings['save_history'] == 'yes'
    if watch_settings['link_depth'] != '':
        cfg['link_depth'] = int(watch_settings['link_depth'])
    cfg['include_filter_list'] = _as_lines(watch_settings['include_filters'])
    cfg['subtractive_selector_list'] = _as_lines(watch_settings['subtractive_selectors'])
    return cfg


def load(datastore_path):
    from changedetectionio.pluggy_interface import load_plugin_settings
    return clean(load_plugin_settings(datastore_path, PLUGIN_ID))


def load_watch(watch):
    try:
        with open(os.path.join(watch.data_dir, WATCH_FILE), encoding='utf-8') as f:
            return clean_watch(json.load(f))
    except (OSError, ValueError):
        return clean_watch({})


def save_watch(watch, values):
    os.makedirs(watch.data_dir, exist_ok=True)
    with open(os.path.join(watch.data_dir, WATCH_FILE), 'w', encoding='utf-8') as f:
        json.dump(clean_watch(values), f, indent=2)
