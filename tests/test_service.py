import threading

import pytest

from cd_linked_content import history, scraper, service, settings


def make_cfg(global_settings=None, watch_settings=None):
    return settings.effective(settings.clean(global_settings or {}), watch_settings or {})


CFG = make_cfg()


def item(url='https://a.example/x', text='Role: Engineer', **extra):
    base = {'url': url, 'method': 'requests', 'status_code': 200, 'text': text, 'error': None, 'truncated': False,
            'depth': 0, 'parent': None, 'kind': 'new'}
    return {**base, **extra}


def payload(*items, history_on=False):
    return {'links': list(items) or [item()], 'source': 'both', 'method': 'requests', 'found': 1, 'history': history_on}


# --- prompt block --------------------------------------------------------------------------------

def test_prompt_block_has_the_scraped_text_and_skips_empty_pages():
    block = service.prompt_block(payload(), CFG)
    assert 'https://a.example/x' in block and 'Role: Engineer' in block
    assert service.prompt_block(payload(item(text='', error='boom')), CFG) == ''
    assert service.prompt_block({'links': []}, CFG) == ''
    assert service.prompt_block(None, CFG) == ''


def test_prompt_block_can_be_switched_off():
    assert service.prompt_block(payload(), make_cfg({'send_to_llm': False})) == ''


def test_prompt_block_only_sends_links_matching_the_llm_regex():
    pl = payload(item('https://a.example/jobs/1', 'job one'), item('https://a.example/blog/2', 'blog two'))
    block = service.prompt_block(pl, make_cfg({'llm_link_regex': '/jobs/'}))
    assert 'job one' in block and 'blog two' not in block
    assert service.prompt_block(pl, make_cfg({'llm_link_regex': '/nope/'})) == ''


def test_prompt_block_with_history_sends_only_what_changed():
    pl = payload(
        item('https://a.example/new', 'brand new page', history={'state': 'first', 'previous_at': None, 'diff': ''}),
        item('https://a.example/changed', 'whole changed page', history={'state': 'changed', 'previous_at': 1700000000,
                                                                       'diff': '-old line\n+new line'}),
        item('https://a.example/same', 'whole same page', history={'state': 'unchanged', 'previous_at': 1700000000, 'diff': ''}),
        history_on=True)
    block = service.prompt_block(pl, make_cfg({'save_history': True}))
    assert 'brand new page' in block
    assert '-old line\n+new line' in block and 'whole changed page' not in block
    assert 'whole same page' not in block and 'same' not in block.split('Linked page:')[-1]


def test_prompt_block_names_the_parent_of_deeper_links():
    block = service.prompt_block(payload(item('https://a.example/deep', 'deep text', depth=1, parent='https://a.example/p')), CFG)
    assert 'found on https://a.example/p' in block


# --- tab -----------------------------------------------------------------------------------------

def test_tab_html_escapes_scraped_content():
    html = str(service.tab_html(payload(item(text='<script>alert(1)</script>', url='https://a.example/?q="><b>')), CFG))
    assert '<script>' not in html and '&lt;script&gt;' in html and '"><b>' not in html


def test_tab_html_states():
    assert 'No new links' in str(service.tab_html({'links': [], 'source': 'html', 'method': 'requests', 'found': 0}, CFG))
    assert 'switched off' in str(service.tab_html({'links': []}, make_cfg({'enabled': False})))
    assert 'No linked content was saved' in str(service.tab_html(None, CFG))


def test_tab_html_shows_history_state_and_a_coloured_diff_escaped():
    pl = payload(item(history={'state': 'changed', 'previous_at': 1700000000, 'diff': '-<b>old</b>\n+new'}), history_on=True)
    html = str(service.tab_html(pl, CFG))
    assert 'changed since' in html and 'linked-content-removed' in html and 'linked-content-added' in html
    assert '<b>old</b>' not in html and '&lt;b&gt;old' in html


# --- fake watch ----------------------------------------------------------------------------------

class FakeWatch(dict):
    """The few Watch members the add-on uses."""

    def __init__(self, snapshots, html, data_dir='/nonexistent', url='https://a.example/'):
        super().__init__(uuid='u1', url=url)
        self.history = dict.fromkeys(snapshots)
        self.data_dir = str(data_dir)
        self._snapshots, self._html = snapshots, html

    def get_history_snapshot(self, timestamp):
        return self._snapshots[timestamp]

    def get_fetched_html(self, timestamp):
        return self._html.get(timestamp, False)


class Handler:
    class fetcher:
        content = '<a href="/new">n</a>'


def run_in_thread(name, fn):
    out = []
    thread = threading.Thread(target=lambda: out.append(fn()), name=name)
    thread.start()
    thread.join()
    return out[0]


def flipped_back_watch():
    # snapshot 3 is the page without "job"; the page then flips back to what snapshot 2 had
    return FakeWatch({1: 'a', 2: 'a\njob', 3: 'a'}, {2: '<a href="/old">o</a>', 3: '<a href="/old">o</a>'})


def test_worker_summary_uses_the_live_fetch_even_when_the_content_equals_an_older_snapshot():
    watch = flipped_back_watch()
    service.remember_handler(watch, Handler())
    service._live['u1']['html'] = Handler.fetcher.content
    try:
        ctx = service.context_for_summary(watch, 'a\njob')
    finally:
        service.forget_handler(watch)
    assert (ctx['from_version'], ctx['to_version']) == (3, None)
    assert ctx['new_html'] == Handler.fetcher.content and ctx['old_html'] == '<a href="/old">o</a>'


def test_diff_page_summary_looks_the_versions_up_from_the_text():
    watch = flipped_back_watch()
    service.remember_handler(watch, Handler())  # a recheck of the same watch happening at the same time
    try:
        ctx = run_in_thread('llm-summary_0', lambda: service.context_for_summary(watch, 'a\njob'))
    finally:
        service.forget_handler(watch)
    assert (ctx['from_version'], ctx['to_version']) == (1, 2)
    assert ctx['new_html'] == '<a href="/old">o</a>'


def test_summary_without_a_live_handler_looks_the_versions_up_from_the_text():
    ctx = service.context_for_summary(flipped_back_watch(), 'a\njob')
    assert (ctx['from_version'], ctx['to_version']) == (1, 2)


def test_remember_handler_keeps_the_html_after_the_worker_clears_it():
    import asyncio

    class H:
        fetcher = type('F', (), {'content': None})()

        async def call_browser(self):
            self.fetcher.content = '<a href="/x">x</a>'

    watch, handler = FakeWatch({1: 'a'}, {}), H()
    service.remember_handler(watch, handler)
    asyncio.run(handler.call_browser())
    handler.fetcher.content = None  # what the worker's clear_content() does
    entry = service.forget_handler(watch)
    assert entry['html'] == '<a href="/x">x</a>'


# --- seeds ---------------------------------------------------------------------------------------

PAGE_OLD = '<a href="/jobs/1">1</a>'
PAGE_NEW = '<a href="/jobs/1">1</a><a href="/jobs/2">2</a><a href="/blog/3">3</a><a href="https://other.example/jobs/9">x</a>'


def ctx_for(old_html=PAGE_OLD, new_html=PAGE_NEW, changed=True, from_version=1):
    return {'from_version': from_version, 'to_version': 2, 'old_text': 'a', 'new_text': 'a b', 'old_html': old_html,
            'new_html': new_html, 'changed': changed}


def test_seeds_are_the_new_links_after_the_per_watch_regexes():
    watch = FakeWatch({}, {})
    cfg = make_cfg({'link_source': 'html'}, {'link_include_regex': '/jobs/'})
    seeds, found = service.seeds_for(watch, ctx_for(), cfg)
    assert seeds == [('https://a.example/jobs/2', 'new')] and found == 3
    cfg = make_cfg({'link_source': 'html'}, {'link_exclude_regex': '/jobs/'})
    assert service.seeds_for(watch, ctx_for(), cfg)[0] == [('https://a.example/blog/3', 'new')]


def test_always_scrape_regex_picks_links_that_are_not_new_and_ignores_only_follow():
    watch = FakeWatch({}, {})
    cfg = make_cfg({'link_source': 'html'}, {'link_include_regex': '/blog/', 'always_scrape_regex': '/jobs/1$'})
    seeds, _ = service.seeds_for(watch, ctx_for(), cfg)
    assert seeds == [('https://a.example/blog/3', 'new'), ('https://a.example/jobs/1', 'always')]


def test_always_scrape_still_obeys_same_domain_and_never_follow():
    watch = FakeWatch({}, {})
    cfg = make_cfg({'link_source': 'html'}, {'always_scrape_regex': '/jobs/', 'link_exclude_regex': '/jobs/2'})
    urls = [u for u, kind in service.seeds_for(watch, ctx_for(), cfg)[0] if kind == 'always']
    assert urls == ['https://a.example/jobs/1']


def test_always_scrape_runs_on_an_unchanged_page_without_new_links():
    watch = FakeWatch({}, {})
    cfg = make_cfg({'link_source': 'html'}, {'always_scrape_regex': '/jobs/'})
    seeds, found = service.seeds_for(watch, ctx_for(old_html='', changed=False), cfg)
    assert [k for _, k in seeds] == ['always', 'always'] and found == 0


# --- crawl, depth, history, cache ------------------------------------------------------------------

class FakeWeb:
    """scraper.scrape stand-in: url -> (text, [links])."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, cfg, want_links=False):
        self.calls.append((url, want_links))
        text, found = self.pages.get(url, ('', []))
        return {'url': url, 'method': 'requests', 'status_code': 200 if text else 404, 'text': text,
                'links': list(found) if want_links else [], 'error': None if text else 'missing', 'truncated': False,
                'fetched_at': 1}


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb({
        'https://a.example/jobs/2': ('job two', ['https://a.example/apply/2', 'https://a.example/jobs/2',
                                                 'https://elsewhere.example/x']),
        'https://a.example/apply/2': ('apply form', ['https://a.example/legal']),
        'https://a.example/legal': ('legal text', []),
    })
    monkeypatch.setattr(scraper, 'scrape', fake)
    return fake


JOBS_ONLY = {'link_include_regex': '/jobs/'}  # the sample page has two new same-domain links, keep one


def seeds2():
    return [('https://a.example/jobs/2', 'new')]


def test_depth_0_does_not_follow_links_found_in_linked_pages(web):
    results = service.crawl(FakeWatch({}, {}), seeds2(), make_cfg({'link_depth': 0}))
    assert [r['url'] for r in results] == ['https://a.example/jobs/2']
    assert web.calls == [('https://a.example/jobs/2', False)]


def test_depth_1_follows_one_level_only_on_the_same_domain_without_repeats(web):
    results = service.crawl(FakeWatch({}, {}), seeds2(), make_cfg({'link_depth': 1}))
    assert [(r['url'], r['depth'], r['parent']) for r in results] == [
        ('https://a.example/jobs/2', 0, None), ('https://a.example/apply/2', 1, 'https://a.example/jobs/2')]
    assert results[1]['kind'] == 'linked'
    assert ('https://a.example/legal', False) not in web.calls


def test_depth_2_goes_one_further_and_depth_1_pages_are_asked_for_their_links_only_when_needed(web):
    cfg = make_cfg({'link_depth': 2})
    results = service.crawl(FakeWatch({}, {}), seeds2(), cfg)
    assert [r['depth'] for r in results] == [0, 1, 2]
    assert ('https://a.example/legal', False) in web.calls  # the deepest level does not need its links


def test_deeper_links_obey_the_only_follow_and_never_follow_regexes(web):
    cfg = make_cfg({'link_depth': 2}, {'link_exclude_regex': '/apply/'})
    assert [r['url'] for r in service.crawl(FakeWatch({}, {}), seeds2(), cfg)] == ['https://a.example/jobs/2']


def test_max_links_caps_all_depths_together(web):
    cfg = make_cfg({'link_depth': 2, 'max_links': 2})
    assert len(service.crawl(FakeWatch({}, {}), seeds2(), cfg)) == 2


def test_linked_content_saves_a_history_and_reports_what_changed(tmp_path, monkeypatch):
    watch = FakeWatch({}, {}, data_dir=tmp_path)
    cfg = make_cfg({'link_source': 'html', 'save_history': True}, JOBS_ONLY)
    pages = {'https://a.example/jobs/2': ('line one\nline two', [])}
    monkeypatch.setattr(scraper, 'scrape', FakeWeb(pages))

    first = service.linked_content(watch, ctx_for(), cfg)
    assert first['links'][0]['history']['state'] == 'first' and first['history'] is True

    pages['https://a.example/jobs/2'] = ('line one\nline THREE', [])
    second = service.linked_content(watch, ctx_for(from_version=2), cfg)  # the next change, not the cached one
    state = second['links'][0]['history']
    assert state['state'] == 'changed' and '-line two' in state['diff'] and '+line THREE' in state['diff']

    third = service.linked_content(watch, ctx_for(from_version=3), cfg)
    assert third['links'][0]['history']['state'] == 'unchanged'
    assert len(history.versions(watch, 'https://a.example/jobs/2')) == 2


def test_without_history_nothing_is_saved_and_the_result_is_cached(tmp_path, monkeypatch):
    watch = FakeWatch({}, {}, data_dir=tmp_path)
    cfg = make_cfg({'link_source': 'html'}, JOBS_ONLY)
    fake = FakeWeb({'https://a.example/jobs/2': ('job two', [])})
    monkeypatch.setattr(scraper, 'scrape', fake)
    first = service.linked_content(watch, ctx_for(), cfg)
    assert 'history' not in first['links'][0] and history.versions(watch, 'https://a.example/jobs/2') == []
    service.linked_content(watch, ctx_for(), cfg)
    assert len(fake.calls) == 1


def test_an_old_version_is_never_scraped_now_but_a_saved_result_is_shown(tmp_path, monkeypatch):
    watch = FakeWatch({}, {}, data_dir=tmp_path)
    cfg = make_cfg({'link_source': 'html'}, JOBS_ONLY)
    fake = FakeWeb({'https://a.example/jobs/2': ('job two', [])})
    monkeypatch.setattr(scraper, 'scrape', fake)
    assert service.linked_content(watch, ctx_for(), cfg, allow_scrape=False) is None and fake.calls == []
    service.linked_content(watch, ctx_for(), cfg)
    assert service.linked_content(watch, ctx_for(), cfg, allow_scrape=False)['links'][0]['text'] == 'job two'


def test_may_scrape_now_only_for_the_newest_version():
    watch = FakeWatch({1: 'a', 2: 'b'}, {})
    assert service.may_scrape_now(watch, {'to_version': 2}) and service.may_scrape_now(watch, {'to_version': None})
    assert not service.may_scrape_now(watch, {'to_version': 1})


# --- after every check -----------------------------------------------------------------------------

def run_after_check(watch, cfg_global, cfg_watch=None, live_html=None):
    settings.save_watch(watch, cfg_watch or {})
    service.settings_for = lambda w, d=None: settings.effective(settings.clean(cfg_global), settings.load_watch(w))
    service._after_check_job(watch, None, live_html)


@pytest.fixture(autouse=True)
def restore_settings_for():
    original = service.settings_for
    yield
    service.settings_for = original


def test_after_check_first_sight_only_records_the_version(tmp_path, monkeypatch):
    fake = FakeWeb({'https://a.example/jobs/2': ('job two', [])})
    monkeypatch.setattr(scraper, 'scrape', fake)
    watch = FakeWatch({1: 'a', 2: 'a b'}, {1: PAGE_OLD, 2: PAGE_NEW}, data_dir=tmp_path)
    run_after_check(watch, {'link_source': 'html', 'enabled': True})
    assert fake.calls == [] and service._load_state(watch) == {'last_version': '2'}


def test_after_check_scrapes_the_new_links_after_a_change_and_not_again_when_nothing_changed(tmp_path, monkeypatch):
    fake = FakeWeb({'https://a.example/jobs/2': ('job two', []), 'https://a.example/blog/3': ('blog', [])})
    monkeypatch.setattr(scraper, 'scrape', fake)
    watch = FakeWatch({1: 'a', 2: 'a b'}, {1: PAGE_OLD, 2: PAGE_NEW}, data_dir=tmp_path)
    service._save_state(watch, {'last_version': '1'})
    run_after_check(watch, {'link_source': 'html', 'save_history': True})
    assert sorted(u for u, _ in fake.calls) == ['https://a.example/blog/3', 'https://a.example/jobs/2']
    assert history.latest(watch, 'https://a.example/jobs/2')[1] == 'job two'
    run_after_check(watch, {'link_source': 'html', 'save_history': True})
    assert len(fake.calls) == 2


def test_after_check_scrapes_always_links_every_check_using_the_live_html(tmp_path, monkeypatch):
    fake = FakeWeb({'https://a.example/jobs/1': ('job one', [])})
    monkeypatch.setattr(scraper, 'scrape', fake)
    watch = FakeWatch({1: 'a'}, {1: '<a href="/old">o</a>'}, data_dir=tmp_path)
    service._save_state(watch, {'last_version': '1'})  # unchanged page
    run_after_check(watch, {'link_source': 'html', 'save_history': True}, {'always_scrape_regex': '/jobs/'},
                    live_html='<a href="/jobs/1">1</a>')
    assert [u for u, _ in fake.calls] == ['https://a.example/jobs/1']
    assert history.latest(watch, 'https://a.example/jobs/1')[1] == 'job one'


def test_after_check_does_nothing_when_disabled_or_there_is_no_change_and_no_always_regex(tmp_path, monkeypatch):
    fake = FakeWeb({'https://a.example/jobs/2': ('job two', [])})
    monkeypatch.setattr(scraper, 'scrape', fake)
    watch = FakeWatch({1: 'a', 2: 'a b'}, {1: PAGE_OLD, 2: PAGE_NEW}, data_dir=tmp_path)
    service._save_state(watch, {'last_version': '1'})
    run_after_check(watch, {'link_source': 'html', 'enabled': False})
    assert fake.calls == []
    service._save_state(watch, {'last_version': '2'})
    run_after_check(watch, {'link_source': 'html'})
    assert fake.calls == []


def test_a_page_that_flips_back_to_an_earlier_state_is_scraped_again(tmp_path, monkeypatch):
    watch = FakeWatch({}, {}, data_dir=tmp_path)
    cfg = make_cfg({'link_source': 'html', 'save_history': True}, JOBS_ONLY)
    pages = {'https://a.example/jobs/2': ('old content', [])}
    fake = FakeWeb(pages)
    monkeypatch.setattr(scraper, 'scrape', fake)
    service.linked_content(watch, ctx_for(from_version=1), cfg)
    pages['https://a.example/jobs/2'] = ('new content', [])
    # same page text and links as the first change, but it is a later change (starts from another version)
    again = service.linked_content(watch, ctx_for(from_version=3), cfg)
    assert again['links'][0]['text'] == 'new content' and again['links'][0]['history']['state'] == 'changed'
    assert len(fake.calls) == 2
