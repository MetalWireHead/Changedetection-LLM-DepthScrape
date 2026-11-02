"""The add-on's pages: the 'Linked Content' tab of the diff page and of the watch edit page.

Neither page's tabs can be extended by a hook (they are fixed in the templates), so the tabs are added by scripts
injected through get_html_head_extras(). The diff tab reads a small JSON endpoint; the watch tab is plain form fields
inside the edit form, saved by wrapping the edit view so they go through the normal Save button.
"""
import functools
import json
import time
from concurrent.futures import ThreadPoolExecutor

from loguru import logger
from markupsafe import escape

from . import service, settings as settings_module

ENDPOINT = 'linked_content_tab'
EDIT_ENDPOINT = 'ui.ui_edit.edit_page'
DIFF_ENDPOINT = 'ui.ui_diff.diff_history_page'

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='linked-content')
_jobs = {}
_registered = False

STYLE = """<style>
.linked-content-item summary { margin: .6em 0; }
.linked-content-text, .linked-content-diff { white-space: pre-wrap; }
.linked-content-diff span { display: block; }
.linked-content-added { background: rgba(60, 170, 80, .22); }
.linked-content-removed { background: rgba(220, 70, 70, .22); }
#linked-content-settings .pure-form-message-inline { display: block; opacity: .75; margin: .2em 0 .8em; }
</style>"""

TAB_JS = r"""
(function () {
  var ENDPOINT = %(endpoint)s;
  function ready(fn) { document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn(); }
  ready(function () {
    var tabList = document.querySelector('.tabs ul'), ui = document.getElementById('diff-ui');
    var from = document.getElementById('diff-from-version'), to = document.getElementById('diff-to-version');
    var form = document.getElementById('diff-form');
    if (!tabList || !ui || !from || !to || !form || !document.getElementById('text-tab')) { return; }
    var uuid = form.getAttribute('action').split('?')[0].replace(/\/+$/, '').split('/').pop();

    var li = document.createElement('li');
    li.className = 'tab'; li.id = 'linked-content-tab';
    li.innerHTML = '<a href="#linked-content"></a>';
    li.firstChild.textContent = 'Linked Content';
    var extract = document.getElementById('extract-tab');
    extract ? tabList.insertBefore(li, extract) : tabList.appendChild(li);

    var pane = document.createElement('div');
    pane.className = 'tab-pane-inner'; pane.id = 'linked-content';
    pane.innerHTML = '<div id="linked-content-body"></div>';
    ui.appendChild(pane);
    var body = pane.firstChild, loadedFor = null, timer = null;

    function load() {
      var key = from.value + '>' + to.value;
      if (loadedFor === key) { return; }
      loadedFor = key; clearTimeout(timer);
      body.textContent = 'Scraping the links of this change...';
      (function poll() {
        var url = ENDPOINT.replace('__UUID__', encodeURIComponent(uuid)) +
                  '?from_version=' + encodeURIComponent(from.value) + '&to_version=' + encodeURIComponent(to.value);
        fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (data) {
          if (data.status === 'pending') { timer = setTimeout(poll, 1500); }
          else if (data.status === 'done') { body.innerHTML = data.html; }
          else { loadedFor = null; body.textContent = 'Could not load linked content: ' + (data.error || 'unknown error'); }
        }).catch(function (e) { loadedFor = null; body.textContent = 'Could not load linked content: ' + e; });
      })();
    }

    function onHash() {
      if (location.hash === '#linked-content') {
        li.classList.add('active');
        load();
      }
    }
    window.addEventListener('hashchange', onHash);
    onHash();
  });
})();
"""

WATCH_TAB_JS = r"""
(function () {
  var PANE = %(pane)s;
  function ready(fn) { document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn(); }
  ready(function () {
    var tabList = document.querySelector('.tabs ul'), form = document.querySelector('.box-wrap.inner form');
    if (!tabList || !form) { return; }

    var li = document.createElement('li');
    li.className = 'tab'; li.id = 'linked-content-settings-tab';
    li.innerHTML = '<a href="#linked-content-settings"></a>';
    li.firstChild.textContent = 'Linked Content';
    var notifications = tabList.querySelector('a[href="#notifications"]');
    notifications ? tabList.insertBefore(li, notifications.parentElement) : tabList.appendChild(li);

    var pane = document.createElement('div');
    pane.className = 'tab-pane-inner'; pane.id = 'linked-content-settings';
    pane.innerHTML = PANE;
    form.appendChild(pane);

    function onHash() { if (location.hash === '#linked-content-settings') { li.classList.add('active'); } }
    window.addEventListener('hashchange', onHash);
    onHash();
  });
})();
"""


def pane_html(watch, global_cfg):
    """The per-watch tab: plain form fields (named lc_*) that are posted together with the rest of the edit form."""
    w = settings_module.load_watch(watch)

    def field(name, label, help_text, input_html):
        return (f'<div class="pure-control-group"><label for="lc_{name}">{escape(label)}</label>{input_html}'
                f'<span class="pure-form-message-inline">{escape(help_text)}</span></div>')

    def text_input(name, placeholder=''):
        return (f'<input type="text" id="lc_{name}" name="lc_{name}" value="{escape(w[name])}" '
                f'placeholder="{escape(placeholder)}" class="m-d">')

    def textarea(name, rows=4):
        return f'<textarea id="lc_{name}" name="lc_{name}" rows="{rows}" class="m-d">{escape(w[name])}</textarea>'

    history_now = 'on' if global_cfg['save_history'] else 'off'
    history_select = (
        '<select id="lc_save_history" name="lc_save_history">'
        f'<option value=""{" selected" if w["save_history"] == "" else ""}>Use the global setting (currently {history_now})</option>'
        f'<option value="yes"{" selected" if w["save_history"] == "yes" else ""}>Save a history for this watch</option>'
        f'<option value="no"{" selected" if w["save_history"] == "no" else ""}>Do not save a history for this watch</option>'
        '</select>')
    depth_input = (f'<input type="number" id="lc_link_depth" name="lc_link_depth" min="0" max="{settings_module.MAX_LINK_DEPTH}" '
                   f'value="{escape(w["link_depth"])}" placeholder="global: {global_cfg["link_depth"]}" style="width:10em">')

    return ('<input type="hidden" name="lc_present" value="1"><fieldset>'
            + field('link_include_regex', 'Only follow links matching this regex',
                    'Empty = follow every new link (the same-domain rule in Settings > Linked Content still applies).',
                    text_input('link_include_regex'))
            + field('link_exclude_regex', 'Never follow links matching this regex', 'Wins over the other two regexes.',
                    text_input('link_exclude_regex'))
            + field('always_scrape_regex', 'Always scrape links matching this regex, even if they are not new',
                    'Checked against every link on the page after every check. Not limited by "only follow".',
                    text_input('always_scrape_regex'))
            + field('include_filters', 'Include filters for linked pages (one per line)',
                    'CSS selector, xpath:, xpath1:, json: or jq: - same syntax as the watch filters. Empty = the whole page.',
                    textarea('include_filters'))
            + field('subtractive_selectors', 'Remove elements from linked pages (one per line)',
                    'CSS selector or xpath:. Links inside removed elements are not followed either.',
                    textarea('subtractive_selectors'))
            + field('save_history', 'Save a history of linked content',
                    'With a history the AI summary only gets what changed in a linked page since the previous scrape.',
                    history_select)
            + field('link_depth', 'Link depth',
                    f'0 = only the new links, 1 = also the links found in those pages, ... up to {settings_module.MAX_LINK_DEPTH}. '
                    'Empty = the global setting.', depth_input)
            + '</fieldset>')


def _run_job(key, uuid, from_version, to_version):
    try:
        datastore = service.get_datastore()
        watch = datastore.data['watching'][uuid]
        payload, cfg = service.payload_for_versions(watch, datastore, from_version, to_version)
        _jobs[key] = {'status': 'done', 'html': str(service.tab_html(payload, cfg)), 'at': time.time()}
    except Exception as e:
        logger.exception(f"linked_content: tab job failed for {uuid}")
        _jobs[key] = {'status': 'error', 'error': str(e), 'at': time.time()}


def _wrap_edit_view(app):
    """Save the Linked Content tab of the edit page together with the rest of the form."""
    from flask import current_app, flash, request

    original = app.view_functions.get(EDIT_ENDPOINT)
    if original is None or getattr(original, '_linked_content', False):
        return

    @functools.wraps(original)
    def edit_page(*args, **kwargs):
        if request.method == 'POST' and 'lc_present' in request.form:
            datastore = current_app.config['DATASTORE']
            watch = datastore.data['watching'].get(kwargs.get('uuid'))
            if watch:
                values = settings_module.clean_watch({k[3:]: v for k, v in request.form.items() if k.startswith('lc_')})
                settings_module.save_watch(watch, values)
                for name in settings_module.invalid_regexes(values):
                    flash(f'Linked Content: the regular expression in "{name}" is not valid and is ignored.', 'error')
        return original(*args, **kwargs)

    edit_page._linked_content = True
    app.view_functions[EDIT_ENDPOINT] = edit_page


def register_routes():
    """Add the endpoint and the edit view wrapper to the Flask app. Safe to call from any hook, any number of times.

    The rule goes straight into the app's URL map instead of through add_url_rule()/a Blueprint: Flask refuses
    those once the app has served a request, and the first hook that runs (get_html_head_extras) runs inside one.
    """
    global _registered
    if _registered:
        return True
    from flask import current_app, jsonify, request
    from werkzeug.routing import Rule
    from changedetectionio import flask_app
    from changedetectionio.auth_decorator import login_optionally_required

    @login_optionally_required
    def linked_content_tab(uuid):
        datastore = current_app.config['DATASTORE']
        watch = datastore.data['watching'].get(uuid)
        from_version, to_version = request.args.get('from_version'), request.args.get('to_version')
        if not watch or not from_version or not to_version:
            return jsonify(status='error', error='Unknown watch or version'), 404
        versions = [str(k) for k in watch.history.keys()]
        if from_version not in versions or to_version not in versions:
            return jsonify(status='error', error='Unknown version'), 404

        key = (uuid, from_version, to_version)
        job = _jobs.get(key)
        if job is None:
            _jobs[key] = {'status': 'pending'}
            _pool.submit(_run_job, key, uuid, from_version, to_version)
            return jsonify(status='pending')
        if job['status'] == 'pending':
            return jsonify(status='pending')
        # Delivered once; looking again just re-reads the on-disk cache, so nothing accumulates here
        _jobs.pop(key, None)
        return jsonify(job)

    app = flask_app.app
    app.view_functions[ENDPOINT] = linked_content_tab
    app.url_map.add(Rule('/linked-content/<string:uuid>', endpoint=ENDPOINT, methods=['GET']))
    _wrap_edit_view(app)
    _registered = True
    logger.info("linked_content: registered /linked-content endpoint")
    return True


def head_extras():
    """HTML for the <head> of every page, '' unless the add-on is set up. Runs inside a request, so url_for works."""
    from flask import current_app, request, url_for

    if not _registered:
        return ''
    if request.endpoint == EDIT_ENDPOINT:
        datastore = current_app.config['DATASTORE']
        watch = datastore.data['watching'].get((request.view_args or {}).get('uuid'))
        if not watch:
            return ''
        pane = pane_html(watch, service.load_settings(datastore))
        return STYLE + '<script>' + (WATCH_TAB_JS % {'pane': json.dumps(pane)}) + '</script>'
    if request.endpoint == DIFF_ENDPOINT:
        endpoint = url_for(ENDPOINT, uuid='__UUID__')
        return STYLE + '<script>' + (TAB_JS % {'endpoint': json.dumps(endpoint)}) + '</script>'
    return ''
