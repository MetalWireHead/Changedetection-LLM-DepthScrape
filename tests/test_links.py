from cd_linked_content import links

BASE = 'https://jobs.example.com/acme'

OLD_HTML = '<a href="/acme/1">One</a>'
NEW_HTML = """
<html><head><base href="https://jobs.example.com/acme/"></head><body>
  <a href="1">One</a>
  <a href="2">Two</a>
  <button data-href="/acme/3">Three</button>
  <button formaction="/acme/4">Four</button>
  <div onclick="window.location.href='/acme/5'">Five</div>
  <a href="#top">top</a><a href="mailto:a@b.c">mail</a><a href="javascript:void(0)">js</a>
  <a href="/static/app.css">css</a><a href="/acme/2#apply">Two again</a>
</body></html>
"""


def test_html_finds_links_in_buttons_and_onclick_and_resolves_against_base():
    found = links.links_in_html(NEW_HTML, BASE)
    assert found == [
        'https://jobs.example.com/acme/1', 'https://jobs.example.com/acme/2', 'https://jobs.example.com/acme/3',
        'https://jobs.example.com/acme/4', 'https://jobs.example.com/acme/2', 'https://jobs.example.com/acme/5',
    ]


def test_text_links_trim_punctuation_and_only_use_added_lines():
    old = 'see https://a.example/old\nsame line'
    new = 'see https://a.example/old\nsame line\nNew: https://a.example/new.\nand (https://a.example/two), ok'
    assert links.added_lines(old, new) == ['New: https://a.example/new.', 'and (https://a.example/two), ok']
    assert links.new_links('text', '', old, new, '', '') == ['https://a.example/new', 'https://a.example/two']


def test_html_mode_ignores_text_urls_and_text_mode_ignores_html():
    assert links.new_links('html', BASE, 'a', 'a\nsee https://x.example/t', OLD_HTML, NEW_HTML)[0].endswith('/acme/2')
    assert 'https://x.example/t' not in links.new_links('html', BASE, 'a', 'a\nsee https://x.example/t', OLD_HTML, NEW_HTML)
    assert links.new_links('text', BASE, 'a', 'a\nsee https://x.example/t', OLD_HTML, NEW_HTML) == ['https://x.example/t']


def test_html_mode_only_returns_links_missing_from_the_old_html():
    found = links.new_links('html', BASE, '', '', OLD_HTML, NEW_HTML)
    assert 'https://jobs.example.com/acme/1' not in found
    assert found == [f'https://jobs.example.com/acme/{n}' for n in (2, 3, 4, 5)]


def test_html_mode_without_old_html_only_trusts_links_named_in_added_lines():
    found = links.new_links('html', BASE, '', 'we added https://jobs.example.com/acme/3 today', '', NEW_HTML)
    assert found == ['https://jobs.example.com/acme/3']


def test_both_dedupes_keeping_page_order():
    found = links.new_links('both', BASE, 'x', 'x\nhttps://jobs.example.com/acme/3', OLD_HTML, NEW_HTML)
    assert found[0] == 'https://jobs.example.com/acme/3'
    assert len(found) == len(set(found)) == 4


def test_filter_links_same_domain_regex_limit_and_self():
    urls = ['https://jobs.example.com/acme/1', 'https://www.jobs.example.com/acme/2', 'https://other.example/acme/3',
            'https://jobs.example.com/acme', 'https://jobs.example.com/blog/4']
    assert links.filter_links(urls, BASE) == ['https://jobs.example.com/acme/1', 'https://www.jobs.example.com/acme/2',
                                              'https://jobs.example.com/blog/4']
    assert links.filter_links(urls, BASE, same_domain_only=False, include_regex=r'/acme/\d', limit=2) == [
        'https://jobs.example.com/acme/1', 'https://www.jobs.example.com/acme/2']
    assert links.filter_links(urls, BASE, exclude_regex=r'/blog/') == ['https://jobs.example.com/acme/1',
                                                                        'https://www.jobs.example.com/acme/2']
    assert links.filter_links(urls, BASE, include_regex='([bad') == links.filter_links(urls, BASE)
