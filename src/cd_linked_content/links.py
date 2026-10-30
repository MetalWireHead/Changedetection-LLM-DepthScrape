"""Find the links that are new in a change, in the text of the diff and/or in the fetched HTML."""
import re
from urllib.parse import urldefrag, urljoin, urlsplit

URL_IN_TEXT = re.compile(r"""https?://[^\s<>"'`\\^{}|\[\])]+""")
URL_IN_JS = re.compile(r"""(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\(|window\.open\()\s*['"]([^'"]+)['"]""")
TRAILING_PUNCTUATION = '.,;:!?'

# Attributes that hold a navigation target, on a link or on a button/div styled as one
HTML_LINK_XPATH = '|'.join([
    '//a[@href]/@href',
    '//area[@href]/@href',
    '//*[@data-href]/@data-href',
    '//*[@data-url]/@data-url',
    '//*[@data-link]/@data-link',
    '//button[@formaction]/@formaction',
    '//input[@formaction]/@formaction',
])

NOT_A_PAGE_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.avif', '.bmp',
    '.css', '.js', '.mjs', '.map', '.json', '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.mp3', '.mp4', '.webm', '.zip', '.gz', '.tar', '.exe', '.dmg',
)


def normalise(url, base_url=''):
    """Absolute http(s) URL without fragment, or None when it isn't something we can scrape."""
    url = (url or '').strip()
    if not url or url.startswith('#'):
        return None
    url = urljoin(base_url, url) if base_url else url
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        return None
    if parts.path.lower().endswith(NOT_A_PAGE_EXTENSIONS):
        return None
    return url


def links_in_text(text, base_url=''):
    found = []
    for match in URL_IN_TEXT.findall(text or ''):
        url = normalise(match.rstrip(TRAILING_PUNCTUATION), base_url)
        if url:
            found.append(url)
    return found


def added_lines(old_text, new_text):
    """Lines of new_text that are not lines of old_text, so reordered lines are not 'new'."""
    old = set((old_text or '').splitlines())
    return [line for line in (new_text or '').splitlines() if line.strip() and line not in old]


def links_in_html(html, base_url=''):
    """Every navigation target in the HTML, including the ones on buttons and in onclick handlers."""
    if not html or not html.strip():
        return []
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return []

    base = base_url
    base_href = tree.xpath('//base[@href]/@href')
    if base_href:
        base = urljoin(base_url, base_href[0])

    raw = list(tree.xpath(HTML_LINK_XPATH))
    for onclick in tree.xpath('//*[@onclick]/@onclick'):
        raw.extend(URL_IN_JS.findall(onclick))

    found = []
    for item in raw:
        url = normalise(item, base)
        if url:
            found.append(url)
    return found


def _unique(urls):
    return list(dict.fromkeys(urls))


def new_links(link_source, base_url, old_text, new_text, old_html, new_html):
    """New links in a change, in page order. `link_source` is 'text', 'html' or 'both'.

    Text: URLs written in lines that were added. HTML: links in the new HTML that the old HTML did not
    have. When there is no old HTML to compare with (only the newest two fetches are kept), every link
    in the new HTML that also appears in an added line is used, so the whole page is never treated as new.
    """
    found = []
    added = added_lines(old_text, new_text)

    if link_source in ('text', 'both'):
        found += links_in_text('\n'.join(added), base_url)

    if link_source in ('html', 'both') and new_html:
        new_in_html = links_in_html(new_html, base_url)
        if old_html:
            known = set(links_in_html(old_html, base_url))
            found += [u for u in new_in_html if u not in known]
        else:
            added_text = '\n'.join(added)
            found += [u for u in new_in_html if u in added_text]

    return _unique(found)


def filter_links(urls, watch_url, same_domain_only=True, include_regex='', exclude_regex='', limit=10):
    include = _compile(include_regex)
    exclude = _compile(exclude_regex)
    watch_host = _host(watch_url)
    watch_url_normalised = normalise(watch_url)
    kept = []
    for url in urls:
        if url == watch_url_normalised:
            continue
        if same_domain_only and watch_host and _host(url) != watch_host:
            continue
        if include and not include.search(url):
            continue
        if exclude and exclude.search(url):
            continue
        kept.append(url)
    return kept[:limit]


def _compile(pattern):
    if not pattern or not pattern.strip():
        return None
    try:
        return re.compile(pattern.strip())
    except re.error:
        return None


def _host(url):
    host = (urlsplit(url or '').hostname or '').lower()
    return host[4:] if host.startswith('www.') else host
