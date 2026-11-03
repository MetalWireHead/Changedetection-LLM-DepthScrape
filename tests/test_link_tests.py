"""The 'Test link rules' and 'Activate preview' buttons: service.run_link_test / run_preview and the shared decision."""
import pytest

from cd_linked_content import links, scraper, service, settings

WATCH_URL = 'https://a.example/board'
PAGE = """
<a href="/board">self</a>
<a href="/jobs/1">one</a>
<a href="/jobs/2">two</a>
<a href="/blog/3">three</a>
<a href="/about">about</a>
<a href="https://other.example/jobs/9">elsewhere</a>
<a href="/static/app.css">css</a>
"""


class Watch(dict):
    data_dir = '/nonexistent'

    def __init__(self):
        super().__init__(uuid='u1', url=WATCH_URL)


def rx(pattern):
    return links.compile_regex(pattern)


# --- the shared decision ---------------------------------------------------------------------------

def decide(url, **kw):
    return links.decide(url, WATCH_URL, kw.get('same_domain_only', True), rx(kw.get('include', '')),
                        rx(kw.get('exclude', '')), rx(kw.get('always', '')))


def test_decide_default_scrapes_same_domain_links():
    assert decide('https://a.example/jobs/1') == ('scrape', '')
    assert decide('https://www.a.example/jobs/1')[0] == 'scrape'


def test_decide_skip_reasons():
    assert decide(WATCH_URL) == ('skip', 'this is the watch itself')
    assert decide('https://other.example/x')[1].startswith('another domain')
    assert decide('https://a.example/blog/3', include='/jobs/') == ('skip', 'does not match "only follow"')
    assert decide('https://a.example/jobs/1', exclude='/jobs/') == ('skip', 'matches "never follow"')


def test_decide_always_is_its_own_selector_and_never_follow_wins():
    assert decide('https://a.example/blog/3', include='/jobs/', always='/blog/') == ('always', '')
    assert decide('https://a.example/blog/3', always='/blog/', exclude='/blog/')[0] == 'skip'
    assert decide('https://other.example/blog/3', always='/blog/')[0] == 'skip'  # the same-domain rule still applies
    assert decide('https://other.example/blog/3', always='/blog/', same_domain_only=False)[0] == 'always'


def test_filter_links_is_the_decision_with_a_limit():
    urls = ['https://a.example/jobs/1', 'https://a.example/jobs/2', 'https://a.example/blog/3', WATCH_URL]
    assert links.filter_links(urls, WATCH_URL, include_regex='/jobs/', limit=1) == ['https://a.example/jobs/1']
    assert links.filter_links(urls, WATCH_URL) == urls[:3]


# --- Test link rules -------------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    state = {'global': {'link_source': 'html'}, 'fetched': []}
    monkeypatch.setattr(service, 'load_settings', lambda datastore=None: settings.clean(state['global']))
    monkeypatch.setattr(service, 'fetch_watch_page', lambda watch, datastore: (PAGE, 'html_webdriver'))
    return state


def kinds(result):
    return {r['url'].replace('https://a.example', ''): (r['kind'], r['reason'], r['depth']) for r in result['rows']}


def test_link_test_classifies_every_link_on_the_page(env):
    result = service.run_link_test(Watch(), None, {'link_include_regex': '/jobs/', 'always_scrape_regex': '/about'})
    k = kinds(result)
    assert k['/jobs/1'][0] == 'scrape' and k['/jobs/2'][0] == 'scrape'
    assert k['/about'][0] == 'always'
    assert k['/blog/3'] == ('skip', 'does not match "only follow"', 0)
    assert k['/board'][1] == 'this is the watch itself'
    other = [r for r in result['rows'] if r['url'].startswith('https://other.example')][0]
    assert other['kind'] == 'skip' and other['reason'].startswith('another domain')
    assert '/static/app.css' not in k  # not a page, never listed
    assert result['fetched_with'] == 'html_webdriver' and result['pages_scraped'] == 0 and result['errors'] == []


def test_link_test_uses_the_unsaved_values_not_the_saved_ones(env):
    a = service.run_link_test(Watch(), None, {'link_exclude_regex': '/jobs/'})
    b = service.run_link_test(Watch(), None, {})
    assert kinds(a)['/jobs/1'][0] == 'skip' and kinds(b)['/jobs/1'][0] == 'scrape'


def test_link_test_caps_at_max_links(env):
    env['global']['max_links'] = 2
    result = service.run_link_test(Watch(), None, {})
    selected = [r for r in result['rows'] if r['kind'] != 'skip']
    assert len(selected) == 2
    capped = [r for r in result['rows'] if 'max links limit' in r['reason']]
    assert capped and all(r['kind'] == 'skip' for r in capped)


def test_link_test_reports_an_invalid_regex_without_fetching(env, monkeypatch):
    monkeypatch.setattr(service, 'fetch_watch_page', lambda *a: pytest.fail('must not fetch'))
    assert 'link_include_regex' in service.run_link_test(Watch(), None, {'link_include_regex': '([bad'})['error']


def test_link_test_depth_scrapes_the_selected_pages_and_lists_their_links(env, monkeypatch):
    env['global']['link_depth'] = 1
    pages = {
        'https://a.example/jobs/1': ['https://a.example/apply/1', 'https://a.example/jobs/2', 'https://elsewhere.example/x'],
        'https://a.example/jobs/2': ['https://a.example/apply/2'],
    }
    scraped = []

    def fake_scrape(url, cfg, want_links=False, preview=False):
        scraped.append((url, want_links))
        return {'url': url, 'method': 'requests', 'status_code': 200, 'text': 'x', 'error': None, 'truncated': False,
                'links': pages.get(url, []) if want_links else []}

    monkeypatch.setattr(scraper, 'scrape', fake_scrape)
    result = service.run_link_test(Watch(), None, {'link_include_regex': '/jobs/'})
    assert sorted(u for u, _ in scraped) == ['https://a.example/jobs/1', 'https://a.example/jobs/2'] and result['pages_scraped'] == 2
    deeper = [r for r in result['rows'] if r['depth'] == 1]
    by_url = {r['url']: r for r in deeper}
    # the 'only follow' regex applies one level down too: /apply/ is not /jobs/
    assert by_url['https://a.example/apply/1']['kind'] == 'skip' and by_url['https://a.example/apply/1']['parent'].endswith('/jobs/1')
    assert by_url['https://elsewhere.example/x']['reason'].startswith('another domain')
    assert 'https://a.example/jobs/2' not in by_url  # already listed at depth 0


def test_link_test_depth_zero_scrapes_nothing(env, monkeypatch):
    monkeypatch.setattr(scraper, 'scrape', lambda *a, **k: pytest.fail('depth 0 must not scrape'))
    assert service.run_link_test(Watch(), None, {})['pages_scraped'] == 0


def test_link_test_scrape_errors_are_reported(env, monkeypatch):
    env['global']['link_depth'] = 1
    monkeypatch.setattr(scraper, 'scrape', lambda url, cfg, want_links=False, preview=False: {
        'url': url, 'method': 'requests', 'status_code': None, 'text': '', 'links': [], 'error': 'boom', 'truncated': False})
    result = service.run_link_test(Watch(), None, {})
    assert result['errors'] and result['errors'][0]['error'] == 'boom'


# --- Activate preview ------------------------------------------------------------------------------

def test_preview_returns_the_page_before_and_after_the_unsaved_filters(env, monkeypatch):
    seen = {}

    def fake_scrape(url, cfg, want_links=False, preview=False):
        seen.update(url=url, filters=cfg['include_filter_list'], remove=cfg['subtractive_selector_list'], preview=preview)
        return {'url': url, 'method': 'requests', 'status_code': 200, 'text': 'only this', 'unfiltered': 'only this and more',
                'error': None, 'truncated': False, 'links': []}

    monkeypatch.setattr(scraper, 'scrape', fake_scrape)
    result = service.run_preview(Watch(), None, {'include_filters': '.a\n.b', 'subtractive_selectors': 'ul'}, 'https://a.example/jobs/1')
    assert seen == {'url': 'https://a.example/jobs/1', 'filters': ['.a', '.b'], 'remove': ['ul'], 'preview': True}
    assert (result['after'], result['before'], result['error']) == ('only this', 'only this and more', None)
    assert result['include_filters'] == ['.a', '.b'] and result['remove'] == ['ul']


def test_preview_passes_scrape_errors_through(env, monkeypatch):
    monkeypatch.setattr(scraper, 'scrape', lambda url, cfg, want_links=False, preview=False: {
        'url': url, 'method': 'requests', 'status_code': 200, 'text': '', 'unfiltered': 'page', 'links': [],
        'error': 'The include filters matched nothing on this page', 'truncated': False})
    result = service.run_preview(Watch(), None, {'include_filters': '.nope'}, 'https://a.example/jobs/1')
    assert result['error'].startswith('The include filters matched nothing') and result['before'] == 'page'


def test_preview_with_an_invalid_regex_is_refused(env):
    assert 'always_scrape_regex' in service.run_preview(Watch(), None, {'always_scrape_regex': '(('}, 'https://a.example/x')['error']
