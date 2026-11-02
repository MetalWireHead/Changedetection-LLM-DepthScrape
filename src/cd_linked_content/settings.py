"""Settings tab (Settings > Linked Content), stored by changedetection.io in <datastore>/linked_content.json."""
import os

from wtforms import BooleanField, Form, IntegerField, SelectField, StringField, TextAreaField

PLUGIN_ID = 'linked_content'

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
    'link_include_regex': '',
    'link_exclude_regex': '',
    'max_links': 10,
    'max_chars_per_link': 5000,
    'timeout': 30,
    'include_filters': '',
    'subtractive_selectors': '',
}


class LinkedContentForm(Form):
    enabled = BooleanField('Scrape new links found in a diff', default=DEFAULTS['enabled'])
    link_source = SelectField('Look for new links in', choices=LINK_SOURCES, default=DEFAULTS['link_source'])
    scrape_method = SelectField('Scrape new links using', choices=SCRAPE_METHODS, default=DEFAULTS['scrape_method'])
    same_domain_only = BooleanField('Only follow links on the same domain as the watch', default=DEFAULTS['same_domain_only'])
    link_include_regex = StringField('Only follow links matching this regex (optional)', default='')
    link_exclude_regex = StringField('Never follow links matching this regex (optional)', default='')
    max_links = IntegerField('Maximum new links to scrape per change', default=DEFAULTS['max_links'])
    max_chars_per_link = IntegerField('Maximum characters kept per link', default=DEFAULTS['max_chars_per_link'])
    timeout = IntegerField('Timeout per link (seconds)', default=DEFAULTS['timeout'])
    include_filters = TextAreaField(
        'Include filters for linked pages (one per line): CSS selector, xpath:, xpath1:, json: or jq:',
        default='')
    subtractive_selectors = TextAreaField('Remove elements from linked pages (one per line): CSS selector or xpath:',
                                          default='')


def _as_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _as_lines(value):
    return [line.strip() for line in (value or '').splitlines() if line.strip()]


def clean(raw):
    """Saved settings dict -> settings with every key present and sane."""
    raw = raw or {}
    s = dict(DEFAULTS)
    s.update({k: raw[k] for k in DEFAULTS if k in raw and raw[k] is not None})
    s['enabled'] = bool(s['enabled'])
    s['same_domain_only'] = bool(s['same_domain_only'])
    if s['link_source'] not in dict(LINK_SOURCES):
        s['link_source'] = DEFAULTS['link_source']
    if s['scrape_method'] not in dict(SCRAPE_METHODS):
        s['scrape_method'] = DEFAULTS['scrape_method']
    s['max_links'] = _as_int(s['max_links'], DEFAULTS['max_links'], 1, 50)
    s['max_chars_per_link'] = _as_int(s['max_chars_per_link'], DEFAULTS['max_chars_per_link'], 100, 200_000)
    s['timeout'] = _as_int(s['timeout'], DEFAULTS['timeout'], 3, 300)
    s['include_filter_list'] = _as_lines(s['include_filters'])
    s['subtractive_selector_list'] = _as_lines(s['subtractive_selectors'])
    return s


def load(datastore_path):
    from changedetectionio.pluggy_interface import load_plugin_settings
    return clean(load_plugin_settings(datastore_path, PLUGIN_ID))
