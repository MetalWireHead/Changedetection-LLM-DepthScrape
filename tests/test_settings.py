from cd_linked_content import settings


def test_global_settings_defaults_and_clamps():
    cfg = settings.clean({'max_links': '999', 'timeout': 'x', 'scrape_method': 'nope', 'enabled': False, 'link_depth': 99})
    assert (cfg['max_links'], cfg['timeout'], cfg['scrape_method'], cfg['enabled']) == (50, 30, 'requests', False)
    assert cfg['link_depth'] == settings.MAX_LINK_DEPTH
    assert (cfg['send_to_llm'], cfg['save_history'], cfg['link_depth'] == 0) == (True, False, False)
    assert settings.clean({})['link_depth'] == 0


def test_old_global_keys_are_ignored():
    cfg = settings.clean({'link_include_regex': 'x', 'include_filters': '.a'})
    assert 'link_include_regex' not in cfg and 'include_filters' not in cfg


def test_watch_settings_are_cleaned():
    w = settings.clean_watch({'include_filters': ' .a \n\n xpath://b ', 'save_history': 'maybe', 'link_depth': '9',
                              'always_scrape_regex': '  /jobs/  '})
    assert w['include_filters'] == '.a\nxpath://b'
    assert (w['save_history'], w['link_depth'], w['always_scrape_regex']) == ('', str(settings.MAX_LINK_DEPTH), '/jobs/')
    assert settings.clean_watch({'link_depth': 'abc'})['link_depth'] == ''


def test_effective_uses_global_values_unless_the_watch_overrides_them():
    g = settings.clean({'save_history': True, 'link_depth': 2})
    plain = settings.effective(g, {})
    assert (plain['save_history'], plain['link_depth']) == (True, 2)
    own = settings.effective(g, {'save_history': 'no', 'link_depth': '0', 'link_include_regex': 'a', 'include_filters': '.x\n.y',
                                 'subtractive_selectors': 'ul'})
    assert (own['save_history'], own['link_depth']) == (False, 0)
    assert own['link_include_regex'] == 'a' and own['include_filter_list'] == ['.x', '.y']
    assert own['subtractive_selector_list'] == ['ul']
    assert settings.effective(settings.clean({}), {'save_history': 'yes'})['save_history'] is True


def test_invalid_regexes_are_named():
    assert settings.invalid_regexes(settings.clean_watch({'link_include_regex': '([bad', 'always_scrape_regex': 'ok'})) == [
        'link_include_regex']


def test_watch_settings_roundtrip_in_the_watch_folder(tmp_path):
    class W:
        data_dir = str(tmp_path / 'w1')

    settings.save_watch(W, {'always_scrape_regex': '/jobs/', 'link_depth': '1'})
    assert settings.load_watch(W)['always_scrape_regex'] == '/jobs/'
    assert settings.load_watch(W)['link_depth'] == '1'

    class Missing:
        data_dir = str(tmp_path / 'nothing')

    assert settings.load_watch(Missing) == settings.clean_watch({})
