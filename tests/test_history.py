from cd_linked_content import history


class W:
    def __init__(self, path):
        self.data_dir = str(path)


def test_record_first_changed_unchanged(tmp_path):
    w, url = W(tmp_path), 'https://a.example/p'
    assert history.latest(w, url) == (None, '') and history.versions(w, url) == []
    first = history.record(w, url, 'one\ntwo')
    assert first['state'] == 'first' and first['saved_at']
    same = history.record(w, url, 'one\ntwo')
    assert same['state'] == 'unchanged' and same['saved_at'] is None and same['previous_at'] == first['saved_at']
    changed = history.record(w, url, 'one\nthree')
    assert changed['state'] == 'changed' and '-two' in changed['diff'] and '+three' in changed['diff']
    assert history.latest(w, url)[1] == 'one\nthree' and len(history.versions(w, url)) == 2


def test_versions_saved_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    w, url = W(tmp_path), 'https://a.example/p'
    history.save(w, url, 'a', epoch=100)
    history.save(w, url, 'b', epoch=100)
    assert history.versions(w, url) == [100, 101] and history.read(w, url, 101) == 'b'


def test_pages_are_kept_apart_per_url(tmp_path):
    w = W(tmp_path)
    history.save(w, 'https://a.example/1', 'x')
    assert history.versions(w, 'https://a.example/2') == []


def test_diff_is_empty_when_nothing_changed():
    assert history.diff('a\nb', 'a\nb') == ''
