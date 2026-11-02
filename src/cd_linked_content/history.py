"""Saved versions of linked pages: <watch data dir>/linked-content/history/<url hash>/<epoch>.txt"""
import difflib
import hashlib
import os
import time


def _dir(watch, url):
    return os.path.join(watch.data_dir, 'linked-content', 'history', hashlib.sha1(url.encode()).hexdigest()[:16])


def versions(watch, url):
    """Epochs of the saved versions of `url`, oldest first."""
    try:
        return sorted(int(name[:-4]) for name in os.listdir(_dir(watch, url)) if name.endswith('.txt') and name[:-4].isdigit())
    except OSError:
        return []


def read(watch, url, epoch):
    with open(os.path.join(_dir(watch, url), f'{epoch}.txt'), encoding='utf-8') as f:
        return f.read()


def latest(watch, url):
    """(epoch, text) of the newest saved version, (None, '') when there is none."""
    saved = versions(watch, url)
    return (saved[-1], read(watch, url, saved[-1])) if saved else (None, '')


def save(watch, url, text, epoch=None):
    folder = _dir(watch, url)
    os.makedirs(folder, exist_ok=True)
    epoch = int(epoch or time.time())
    while os.path.exists(os.path.join(folder, f'{epoch}.txt')):
        epoch += 1
    with open(os.path.join(folder, f'{epoch}.txt'), 'w', encoding='utf-8') as f:
        f.write(text)
    with open(os.path.join(folder, 'url.txt'), 'w', encoding='utf-8') as f:
        f.write(url)
    return epoch


def diff(old_text, new_text):
    """Unified diff, only the +/- lines and 2 lines of context, '' when nothing changed."""
    lines = list(difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm='', n=2))
    return '\n'.join(lines[2:]) if len(lines) > 2 else ''


def record(watch, url, text):
    """Save `text` as the newest version when it differs from the last one.

    Returns {'state': 'first'|'changed'|'unchanged', 'previous_at': epoch|None, 'diff': str, 'saved_at': epoch|None}.
    """
    previous_at, previous_text = latest(watch, url)
    if previous_at is None:
        return {'state': 'first', 'previous_at': None, 'diff': '', 'saved_at': save(watch, url, text)}
    if previous_text == text:
        return {'state': 'unchanged', 'previous_at': previous_at, 'diff': '', 'saved_at': None}
    return {'state': 'changed', 'previous_at': previous_at, 'diff': diff(previous_text, text),
            'saved_at': save(watch, url, text)}
