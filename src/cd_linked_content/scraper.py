"""Fetch a linked page with changedetection.io's own fetchers and apply this add-on's own filters."""
import asyncio
import re
import time

from loguru import logger

from . import links

JSON_FILTER_PREFIXES = ('json:', 'jq:', 'jqraw:')
PREVIEW_UNFILTERED_CHARS = 100_000


async def _fetch(method, url, timeout):
    """(content, status_code, content_type) from the requests fetcher or the Chrome (webdriver) fetcher."""
    from changedetectionio import content_fetchers
    from changedetectionio.validate_url import validate_fetch_url_async

    # The same gate the app puts in front of every fetch it makes (private/internal addresses, file://, ...), since
    # links come from other people's pages
    await validate_fetch_url_async(url)

    fetcher_class = content_fetchers.html_webdriver if method == 'webdriver' else content_fetchers.html_requests
    fetcher = fetcher_class()
    try:
        await fetcher.run(
            current_include_filters=None,
            empty_pages_are_a_change=False,
            fetch_favicon=False,
            ignore_status_codes=False,
            is_binary=False,
            request_body=None,
            request_headers={},
            request_method='GET',
            timeout=timeout,
            url=url,
            watch_uuid=None,
        )
        headers = {str(k).lower(): v for k, v in (getattr(fetcher, 'headers', None) or {}).items()}
        return fetcher.content or '', fetcher.status_code, headers.get('content-type', '')
    finally:
        try:
            await fetcher.quit()
        except Exception as e:
            logger.debug(f"linked_content: fetcher quit failed for {url}: {e}")


def remove_elements(content, subtractive_selectors):
    from changedetectionio import html_tools

    return html_tools.element_removal(subtractive_selectors, content) if subtractive_selectors else content


def to_text(content, include_filters, content_type=''):
    """Include filters, then plain text. Same rule syntax as the watch filters of changedetection.io."""
    from changedetectionio import html_tools

    if not include_filters:
        return content if 'json' in content_type.lower() else html_tools.html_to_text(content)

    html_parts, text_parts = '', ''
    for rule in include_filters:
        if rule[0] == '/' or rule.startswith('xpath:'):
            html_parts += html_tools.xpath_filter(xpath_filter=rule.replace('xpath:', '', 1),
                                                  html_content=content, append_pretty_line_formatting=True)
        elif rule.startswith('xpath1:'):
            html_parts += html_tools.xpath1_filter(xpath_filter=rule.replace('xpath1:', '', 1),
                                                   html_content=content, append_pretty_line_formatting=True)
        elif rule.startswith(JSON_FILTER_PREFIXES):
            text_parts += html_tools.extract_json_as_string(content=content, json_filter=rule) or ''
        else:
            html_parts += html_tools.include_filters(include_filters=rule, html_content=content,
                                                     append_pretty_line_formatting=True)

    text = html_tools.html_to_text(html_parts) if html_parts.strip() else ''
    return '\n'.join(part for part in (text, text_parts) if part.strip())


def apply_filters(content, include_filters, subtractive_selectors, content_type=''):
    """Linked-page filters -> plain text: remove elements first, then the include filters."""
    return to_text(remove_elements(content, subtractive_selectors), include_filters, content_type)


def tidy(text):
    """Drop the layout indentation html_to_text leaves and collapse runs of blank lines (saves LLM tokens too)."""
    text = '\n'.join(line.strip() for line in text.splitlines())
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def scrape(url, settings, want_links=False, preview=False):
    """Scrape one link. Blocking (call from a worker thread). Never raises, errors are returned.

    With `want_links` the result also has the links found in the page, taken after the 'remove elements' rules ran
    (so removing a menu or footer also removes its links) but before the include filters. With `preview` it also has
    'unfiltered': the page text before the include filters (after 'remove elements'), for the filter preview.
    """
    result = {
        'url': url,
        'method': settings['scrape_method'],
        'status_code': None,
        'text': '',
        'links': [],
        'error': None,
        'truncated': False,
        'fetched_at': int(time.time()),
    }
    try:
        content, status_code, content_type = asyncio.run(_fetch(settings['scrape_method'], url, settings['timeout']))
        result['status_code'] = status_code
        content = remove_elements(content, settings['subtractive_selector_list'])
        if want_links and 'json' not in content_type.lower():
            result['links'] = links.links_in_html(content, url)
        if preview:
            result['unfiltered'] = tidy(to_text(content, [], content_type))[:PREVIEW_UNFILTERED_CHARS]
        text = tidy(to_text(content, settings['include_filter_list'], content_type))
        if not text:
            result['error'] = ('The include filters matched nothing on this page' if settings['include_filter_list']
                               else 'The page had no text content')
        limit = settings['max_chars_per_link']
        if len(text) > limit:
            text, result['truncated'] = text[:limit], True
        result['text'] = text
    except Exception as e:
        logger.warning(f"linked_content: could not scrape {url}: {e}")
        result['error'] = f"{type(e).__name__}: {e}"
    return result
