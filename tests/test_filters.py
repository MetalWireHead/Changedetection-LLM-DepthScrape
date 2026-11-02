"""Needs changedetectionio, run inside the container: docker exec changedetection python -m pytest /addon/tests"""
import pytest

pytest.importorskip('changedetectionio')

from cd_linked_content import scraper  # noqa: E402

HTML = """<html><body><nav>Menu stuff</nav>
<div id="job"><h1>Staff Engineer</h1><p class="ad">Buy now</p><p>Build GPUs</p></div>
<footer>Legal</footer></body></html>"""


def test_no_filters_gives_all_text():
    text = scraper.apply_filters(HTML, [], [])
    assert 'Menu stuff' in text and 'Staff Engineer' in text and 'Legal' in text


def test_css_include_filter_and_css_removal():
    text = scraper.apply_filters(HTML, ['#job'], ['.ad'])
    assert 'Staff Engineer' in text and 'Build GPUs' in text
    assert 'Buy now' not in text and 'Menu stuff' not in text


def test_xpath_include_and_xpath_removal():
    text = scraper.apply_filters(HTML, ['xpath://div[@id="job"]'], ['xpath://p[@class="ad"]'])
    assert 'Staff Engineer' in text and 'Buy now' not in text and 'Legal' not in text


def test_json_and_jq_include_filters():
    data = '{"jobs": [{"title": "A"}, {"title": "B"}]}'
    assert 'A' in scraper.apply_filters(data, ['json:$.jobs[0].title'], [], 'application/json')
    assert 'B' in scraper.apply_filters(data, ['jq:.jobs[1].title'], [], 'application/json')


def test_filter_matching_nothing_is_empty():
    assert scraper.apply_filters(HTML, ['#nope'], []).strip() == ''


def test_tidy_removes_indentation_and_blank_runs():
    assert scraper.tidy('   a  \n\n\n\n      b\n  ') == 'a\n\nb'
