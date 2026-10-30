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
