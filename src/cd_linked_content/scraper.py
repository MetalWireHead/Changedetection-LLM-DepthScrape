"""Fetch a linked page with changedetection.io's own fetchers and apply this add-on's own filters."""
import asyncio
import time

from loguru import logger

JSON_FILTER_PREFIXES = ('json:', 'jq:', 'jqraw:')


async def _fetch(method, url, timeout):
    """(content, status_code, content_type) from the requests fetcher or the Chrome (webdriver) fetcher."""
    from changedetectionio import content_fetchers

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


def apply_filters(content, include_filters, subtractive_selectors, content_type=''):
    """Linked-page filters -> plain text. Same rule syntax as the watch filters of changedetection.io."""
    from changedetectionio import html_tools

    if subtractive_selectors:
        content = html_tools.element_removal(subtractive_selectors, content)

    if not include_filters:
        if 'json' in content_type.lower():
            return content
        return html_tools.html_to_text(content)

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


def scrape(url, settings):
    """Scrape one link. Blocking (call from a worker thread). Never raises, errors are returned."""
    result = {
        'url': url,
        'method': settings['scrape_method'],
        'status_code': None,
        'text': '',
        'error': None,
        'truncated': False,
        'fetched_at': int(time.time()),
    }
    try:
        content, status_code, content_type = asyncio.run(_fetch(settings['scrape_method'], url, settings['timeout']))
        result['status_code'] = status_code
        text = apply_filters(content, settings['include_filter_list'], settings['subtractive_selector_list'],
                             content_type).strip()
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
