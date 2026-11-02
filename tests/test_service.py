from cd_linked_content import service, settings

CFG = settings.clean({})


def payload(**item):
    base = {'url': 'https://a.example/x', 'method': 'requests', 'status_code': 200, 'text': 'Role: Engineer',
            'error': None, 'truncated': False}
    return {'links': [{**base, **item}], 'source': 'both', 'method': 'requests', 'found': 1}


def test_settings_clean_fills_defaults_and_clamps():
    cfg = settings.clean({'max_links': '999', 'timeout': 'x', 'scrape_method': 'nope',
                          'include_filters': ' .a \n\n xpath://b ', 'enabled': False})
    assert (cfg['max_links'], cfg['timeout'], cfg['scrape_method'], cfg['enabled']) == (50, 30, 'requests', False)
    assert cfg['include_filter_list'] == ['.a', 'xpath://b']


def test_prompt_block_has_the_scraped_text_and_skips_empty_pages():
    block = service.prompt_block(payload())
    assert 'https://a.example/x' in block and 'Role: Engineer' in block
    assert service.prompt_block(payload(text='', error='boom')) == ''
    assert service.prompt_block({'links': []}) == ''


def test_tab_html_escapes_scraped_content():
    html = str(service.tab_html(payload(text='<script>alert(1)</script>', url='https://a.example/?q="><b>'), CFG))
    assert '<script>' not in html and '&lt;script&gt;' in html and '"><b>' not in html


def test_tab_html_empty_and_disabled_states():
    assert 'No new links' in str(service.tab_html({'links': [], 'source': 'html', 'method': 'requests', 'found': 0}, CFG))
    assert 'switched off' in str(service.tab_html({'links': []}, settings.clean({'enabled': False})))


class FakeWatch(dict):
    """The few Watch members the add-on uses: history keys, snapshot text and the saved HTML of the newest fetches."""

    def __init__(self, snapshots, html):
        super().__init__(uuid='u1', url='https://a.example/')
        self.history = dict.fromkeys(snapshots)
        self._snapshots, self._html = snapshots, html

    def get_history_snapshot(self, timestamp):
        return self._snapshots[timestamp]

    def get_fetched_html(self, timestamp):
        return self._html.get(timestamp, False)


class Handler:
    class fetcher:
        content = '<a href="/new">n</a>'


def run_in_thread(name, fn):
    import threading
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
