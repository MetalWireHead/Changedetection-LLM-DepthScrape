"""The add-on's pages: the 'Linked Content' tab of the diff page and of the watch edit page.

Neither page's tabs can be extended by a hook (they are fixed in the templates), so the tabs are added by scripts
injected through get_html_head_extras(). The diff tab reads a small JSON endpoint; the watch tab is plain form fields
inside the edit form, saved by wrapping the edit view so they go through the normal Save button.
"""
import functools
import json
import time
import uuid as uuid_module
from concurrent.futures import ThreadPoolExecutor

from loguru import logger
from markupsafe import escape

from . import service, settings as settings_module

ENDPOINT = 'linked_content_tab'
EDIT_ENDPOINT = 'ui.ui_edit.edit_page'
DIFF_ENDPOINT = 'ui.ui_diff.diff_history_page'
TEST_ENDPOINT = 'linked_content_test'
TEST_JOB_KEEP_SECONDS = 900

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='linked-content')
_jobs = {}
_test_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='linked-content-test')
_test_jobs = {}
_registered = False

STYLE = """<style>
.linked-content-item summary { margin: .6em 0; }
.linked-content-text, .linked-content-diff { white-space: pre-wrap; }
.linked-content-diff span { display: block; }
.linked-content-added { background: rgba(60, 170, 80, .22); }
.linked-content-removed { background: rgba(220, 70, 70, .22); }
#linked-content-settings .pure-form-message-inline { display: block; opacity: .75; margin: .2em 0 .8em; }
#linked-content-settings .lc-layout { display: flex; flex-wrap: wrap; gap: 1.5em; align-items: flex-start; }
#linked-content-settings .lc-form { flex: 1 1 26em; min-width: 0; }
#linked-content-settings .lc-panel { flex: 1 1 26em; min-width: 0; position: sticky; top: .5em; max-height: 85vh; overflow: auto;
  border: 1px solid rgba(128, 128, 128, .45); border-radius: 4px; padding: .8em 1em; }
#linked-content-settings .lc-actions { margin: 1em 0; display: flex; gap: .6em; flex-wrap: wrap; align-items: center; }
#linked-content-settings .lc-panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: .4em; }
#linked-content-settings .lc-status { margin: .4em 0; opacity: .85; }
#linked-content-settings .lc-error { color: #c0392b; opacity: 1; }
#linked-content-settings .lc-legend { display: flex; gap: .5em; flex-wrap: wrap; margin: .6em 0; align-items: center; }
#linked-content-settings .lc-badge { display: inline-block; font-size: .75em; padding: .1em .5em; border-radius: 3px; margin-right: .5em;
  color: #fff; white-space: nowrap; }
#linked-content-settings .lc-badge.lc-scrape { background: #27ae60; }
#linked-content-settings .lc-badge.lc-always { background: #2980b9; }
#linked-content-settings .lc-badge.lc-skip { background: #c0392b; }
#linked-content-settings ul.lc-links { padding: 0; margin: 0; }
#linked-content-settings .lc-row { list-style: none; margin: .3em 0; padding: .35em .6em; border-left: 4px solid; word-break: break-all; }
#linked-content-settings .lc-row.lc-scrape { border-color: #27ae60; background: rgba(39, 174, 96, .12); }
#linked-content-settings .lc-row.lc-always { border-color: #2980b9; background: rgba(41, 128, 185, .14); }
#linked-content-settings .lc-row.lc-skip { border-color: #c0392b; background: rgba(192, 57, 43, .1); }
#linked-content-settings .lc-row.lc-selected { outline: 2px solid currentColor; }
#linked-content-settings .lc-reason { font-size: .8em; opacity: .75; }
#linked-content-settings .lc-pick { cursor: pointer; text-decoration: underline; }
#linked-content-settings .lc-minitabs { margin: .6em 0 0; border-bottom: 1px solid rgba(128, 128, 128, .45); }
#linked-content-settings .lc-minitabs a { display: inline-block; padding: .3em .8em; cursor: pointer; text-decoration: none; opacity: .7; }
#linked-content-settings .lc-minitabs a.active { opacity: 1; border-bottom: 2px solid currentColor; font-weight: bold; }
#linked-content-settings .lc-pre { white-space: pre-wrap; max-height: 55vh; overflow: auto; font-size: .85em; margin-top: .6em; }
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
  var TEST_URL = %(test_url)s;
  var NAMES = ['link_include_regex', 'link_exclude_regex', 'always_scrape_regex', 'include_filters',
               'subtractive_selectors', 'save_history', 'link_depth'];
  var LABEL = { scrape: 'will be scraped', always: 'always scraped', skip: 'not scraped' };
  function ready(fn) { document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn(); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }

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

    // ---- the two tests ------------------------------------------------------------------------
    var $ = function (id) { return document.getElementById(id); };
    var panel = $('lc-panel'), status = $('lc-status'), linksView = $('lc-links-view'), previewView = $('lc-preview-view');
    var testButton = $('lc-test-links'), previewButton = $('lc-preview-button');
    var lastTest = null, previewOn = false, selectedUrl = null, seq = 0, throttle = null;

    function values() {
      var v = {};
      NAMES.forEach(function (n) { var e = $('lc_' + n); v[n] = e ? e.value : ''; });
      return v;
    }
    function csrf() {
      var e = form.querySelector('input[name="csrf_token"]');
      return e ? e.value : '';
    }
    function setStatus(message, isError) {
      status.textContent = message || '';
      status.className = 'lc-status' + (isError ? ' lc-error' : '');
    }
    function showPanel(title) {
      panel.style.display = '';
      $('lc-title').textContent = title;
    }
    function run(body) {
      return fetch(TEST_URL, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(body)
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.error) { throw new Error(d.error); }
        return new Promise(function (resolve, reject) {
          (function poll() {
            fetch(TEST_URL + '?job=' + encodeURIComponent(d.job), { credentials: 'same-origin' })
              .then(function (r) { return r.json(); }).then(function (j) {
                if (j.status === 'pending') { setTimeout(poll, 1500); }
                else if (j.status === 'done') { resolve(j.result); }
                else { reject(new Error(j.error || 'The test failed')); }
              }).catch(reject);
          })();
        });
      });
    }

    function linkTest() {
      var mine = ++seq;
      setStatus('Fetching the watch page' + (parseInt($('lc_link_depth').value || '0', 10) > 0
        ? ' and scraping the links to look one level further down' : '') + ' ...');
      return run({ kind: 'links', values: values() }).then(function (res) {
        if (mine !== seq) { return null; }
        if (res.error) { setStatus(res.error, true); return null; }
        lastTest = res; setStatus('');
        return res;
      }).catch(function (e) { if (mine === seq) { setStatus(String(e.message || e), true); } return null; });
    }

    function summary(res) {
      var c = { scrape: 0, always: 0, skip: 0 };
      res.rows.forEach(function (r) { c[r.kind] += 1; });
      return c;
    }

    function renderLinks(res) {
      linksView.textContent = '';
      var c = summary(res);
      var intro = el('p', '', 'Fetched ' + res.watch_url + ' (' + (res.fetched_with || 'watch fetcher') + '): ' +
        res.page_link_count + ' link(s) on the page. Every link is treated as if it were new. ' +
        (res.depth > 0 ? res.pages_scraped + ' page(s) were scraped to look ' + res.depth + ' level(s) further down. ' : ''));
      linksView.appendChild(intro);
      var legend = el('div', 'lc-legend');
      [['scrape', c.scrape], ['always', c.always], ['skip', c.skip]].forEach(function (p) {
        var item = el('span', '');
        item.appendChild(el('span', 'lc-badge lc-' + p[0], LABEL[p[0]]));
        item.appendChild(document.createTextNode(String(p[1])));
        legend.appendChild(item);
      });
      var hide = document.createElement('input'); hide.type = 'checkbox'; hide.id = 'lc-hide-skipped';
      var hideLabel = el('label', '', ' hide the links that are not scraped'); hideLabel.prepend(hide);
      legend.appendChild(hideLabel);
      linksView.appendChild(legend);
      res.errors.forEach(function (e) { linksView.appendChild(el('p', 'lc-error', 'Could not scrape ' + e.url + ': ' + e.error)); });
      var list = el('ul', 'lc-links');
      res.rows.forEach(function (r) {
        var row = el('li', 'lc-row lc-' + r.kind); row.dataset.kind = r.kind;
        row.style.marginLeft = (r.depth * 1.2) + 'em';
        row.appendChild(el('span', 'lc-badge lc-' + r.kind, LABEL[r.kind]));
        var a = el('a', '', r.url); a.href = r.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        row.appendChild(a);
        var note = r.reason || (r.depth ? 'found on ' + r.parent : '');
        if (note) { row.appendChild(el('div', 'lc-reason', note)); }
        list.appendChild(row);
      });
      if (!res.rows.length) { linksView.appendChild(el('p', '', 'No links were found on the page.')); }
      linksView.appendChild(list);
      hide.addEventListener('change', function () {
        list.querySelectorAll('li[data-kind="skip"]').forEach(function (n) { n.style.display = hide.checked ? 'none' : ''; });
      });
    }

    function renderPicker(res) {
      var picker = $('lc-picker'); picker.textContent = '';
      var pickable = res.rows.filter(function (r) { return r.kind !== 'skip'; });
      if (!pickable.length) {
        picker.appendChild(el('p', '', 'No links would be scraped with these rules, so there is nothing to preview. ' +
          'Use "Test link rules" to see why.'));
        $('lc-preview-body').style.display = 'none';
        return;
      }
      picker.appendChild(el('p', '', 'Pick a linked page to preview with the filters on the left (' + pickable.length + '):'));
      var list = el('ul', 'lc-links');
      pickable.forEach(function (r) {
        var row = el('li', 'lc-row lc-' + r.kind); row.style.marginLeft = (r.depth * 1.2) + 'em'; row.dataset.url = r.url;
        row.appendChild(el('span', 'lc-badge lc-' + r.kind, LABEL[r.kind]));
        var pick = el('span', 'lc-pick', r.url);
        row.appendChild(pick);
        row.addEventListener('click', function () { selectUrl(r.url); });
        list.appendChild(row);
      });
      picker.appendChild(list);
      $('lc-preview-body').style.display = '';
      if (!selectedUrl || !pickable.some(function (r) { return r.url === selectedUrl; })) { selectUrl(pickable[0].url); }
      else { markSelected(); }
    }

    function markSelected() {
      $('lc-picker').querySelectorAll('li').forEach(function (n) {
        n.classList.toggle('lc-selected', n.dataset.url === selectedUrl);
      });
    }

    function selectUrl(url) { selectedUrl = url; markSelected(); loadPreview(); }

    function loadPreview() {
      if (!previewOn || !selectedUrl) { return; }
      var mine = ++seq;
      setStatus('Scraping ' + selectedUrl + ' ...');
      run({ kind: 'preview', values: values(), url: selectedUrl }).then(function (res) {
        if (mine !== seq) { return; }
        if (res.error && !res.url) { setStatus(res.error, true); return; }
        var notes = [];
        if (res.status_code) { notes.push('HTTP ' + res.status_code); }
        notes.push('scraped with ' + (res.method === 'webdriver' ? 'Chrome' : 'plain text'));
        notes.push(res.after.length + ' characters after the filters' + (res.truncated ? ' (cut at the maximum per link)' : ''));
        $('lc-preview-note').textContent = notes.join(' - ');
        setStatus(res.error || '', !!res.error);
        $('lc-preview-after').textContent = res.after;
        $('lc-preview-before').textContent = res.before;
      }).catch(function (e) { if (mine === seq) { setStatus(String(e.message || e), true); } });
    }

    function closePanel() {
      panel.style.display = 'none'; previewOn = false; previewButton.classList.remove('pure-button-active'); seq += 1;
    }

    testButton.addEventListener('click', function () {
      previewOn = false; previewButton.classList.remove('pure-button-active');
      showPanel('Test link rules'); previewView.style.display = 'none'; linksView.style.display = '';
      linksView.textContent = '';
      linkTest().then(function (res) { if (res) { renderLinks(res); } });
    });

    previewButton.addEventListener('click', function () {
      if (previewOn) { closePanel(); return; }
      previewOn = true; previewButton.classList.add('pure-button-active');
      showPanel('Preview of the linked pages'); linksView.style.display = 'none'; previewView.style.display = '';
      if (lastTest) { renderPicker(lastTest); return; }
      $('lc-picker').textContent = ''; $('lc-preview-body').style.display = 'none';
      linkTest().then(function (res) { if (res && previewOn) { renderPicker(res); } });
    });

    $('lc-close').addEventListener('click', closePanel);

    panel.querySelectorAll('.lc-minitabs a').forEach(function (a) {
      a.addEventListener('click', function (ev) {
        ev.preventDefault();
        panel.querySelectorAll('.lc-minitabs a').forEach(function (o) { o.classList.toggle('active', o === a); });
        $('lc-preview-after').style.display = a.dataset.tab === 'after' ? '' : 'none';
        $('lc-preview-before').style.display = a.dataset.tab === 'before' ? '' : 'none';
      });
    });

    // Like the stock preview: refresh when a filter field is edited
    ['include_filters', 'subtractive_selectors'].forEach(function (n) {
      ['blur', 'change'].forEach(function (evt) {
        $('lc_' + n).addEventListener(evt, function () {
          if (!previewOn) { return; }
          clearTimeout(throttle); throttle = setTimeout(loadPreview, 600);
        });
      });
    });
  });
})();
"""


ACTIONS_HTML = (
    '<div class="lc-actions">'
    '<button type="button" id="lc-test-links" class="pure-button pure-button-primary button-xsmall">Test link rules</button>'
    '<button type="button" id="lc-preview-button" class="pure-button pure-button-primary button-xsmall">Activate preview</button>'
    '</div>'
    '<span class="pure-form-message-inline">Both use the settings above as they are now, saved or not. '
    '"Test link rules" fetches the watch page again and shows what happens to each link on it; with a link depth above 0 it also '
    'scrapes the pages that would be scraped (at most the maximum links) to look further down. '
    '"Activate preview" lists those links and shows the page of the one you pick before and after the include filters '
    '("remove elements" is applied to both); it refreshes when you edit the filters. Run "Test link rules" again after changing a regex or the depth.</span>')

PANEL_HTML = (
    '<div class="lc-panel" id="lc-panel" style="display:none">'
    '<div class="lc-panel-head"><strong id="lc-title"></strong>'
    '<button type="button" id="lc-close" class="pure-button button-xsmall">Close</button></div>'
    '<div id="lc-status" class="lc-status"></div>'
    '<div id="lc-links-view" style="display:none"></div>'
    '<div id="lc-preview-view" style="display:none"><div id="lc-picker"></div>'
    '<div id="lc-preview-body"><div class="lc-minitabs"><a href="#" data-tab="after" class="active">Content after filters</a>'
    '<a href="#" data-tab="before">Content before include filters</a></div>'
    '<div id="lc-preview-note" class="lc-reason"></div>'
    '<pre id="lc-preview-after" class="lc-pre"></pre><pre id="lc-preview-before" class="lc-pre" style="display:none"></pre>'
    '</div></div></div>')


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

    return ('<input type="hidden" name="lc_present" value="1"><div class="lc-layout"><div class="lc-form">' + ACTIONS_HTML + '<fieldset>'
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
            + '</fieldset></div>' + PANEL_HTML + '</div>')


def _run_job(key, uuid, from_version, to_version):
    try:
        datastore = service.get_datastore()
        watch = datastore.data['watching'][uuid]
        payload, cfg = service.payload_for_versions(watch, datastore, from_version, to_version)
        _jobs[key] = {'status': 'done', 'html': str(service.tab_html(payload, cfg)), 'at': time.time()}
    except Exception as e:
        logger.exception(f"linked_content: tab job failed for {uuid}")
        _jobs[key] = {'status': 'error', 'error': str(e), 'at': time.time()}


def _run_test_job(job_id, uuid, kind, values, url):
    try:
        datastore = service.get_datastore()
        watch = datastore.data['watching'][uuid]
        if kind == 'links':
            result = service.run_link_test(watch, datastore, values)
        else:
            result = service.run_preview(watch, datastore, values, url)
        _test_jobs[job_id].update(status='done', result=result)
    except Exception as e:
        logger.exception(f"linked_content: {kind} test failed for {uuid}")
        _test_jobs[job_id].update(status='error', error=f'{type(e).__name__}: {e}')


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

    @login_optionally_required
    def linked_content_test(uuid):
        """POST starts a 'links' or 'preview' test on the unsaved form values, GET ?job= polls it."""
        datastore = current_app.config['DATASTORE']
        watch = datastore.data['watching'].get(uuid)
        if not watch:
            return jsonify(error='Unknown watch'), 404

        now = time.time()
        for old in [k for k, v in _test_jobs.items() if now - v['at'] > TEST_JOB_KEEP_SECONDS]:
            _test_jobs.pop(old, None)

        if request.method == 'GET':
            job = _test_jobs.get(request.args.get('job', ''))
            if not job or job['uuid'] != uuid:
                return jsonify(status='error', error='Unknown test'), 404
            if job['status'] == 'pending':
                return jsonify(status='pending')
            _test_jobs.pop(request.args['job'], None)
            return jsonify({k: job[k] for k in ('status', 'result', 'error') if k in job})

        body = request.get_json(silent=True) or {}
        kind, form_values, url = body.get('kind'), body.get('values'), body.get('url') or ''
        if kind not in ('links', 'preview') or not isinstance(form_values, dict):
            return jsonify(error='Bad request'), 400
        if kind == 'preview' and not (isinstance(url, str) and url.startswith(('http://', 'https://'))):
            return jsonify(error='A preview needs an http(s) link'), 400
        job_id = uuid_module.uuid4().hex
        _test_jobs[job_id] = {'status': 'pending', 'uuid': uuid, 'at': now}
        _test_pool.submit(_run_test_job, job_id, uuid, kind, {k: str(v) for k, v in form_values.items()}, url)
        return jsonify(job=job_id)

    app = flask_app.app
    app.view_functions[ENDPOINT] = linked_content_tab
    app.view_functions[TEST_ENDPOINT] = linked_content_test
    app.url_map.add(Rule('/linked-content/<string:uuid>', endpoint=ENDPOINT, methods=['GET']))
    app.url_map.add(Rule('/linked-content/test/<string:uuid>', endpoint=TEST_ENDPOINT, methods=['GET', 'POST']))
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
        test_url = url_for(TEST_ENDPOINT, uuid=watch['uuid'])
        return STYLE + '<script>' + (WATCH_TAB_JS % {'pane': json.dumps(pane), 'test_url': json.dumps(test_url)}) + '</script>'
    if request.endpoint == DIFF_ENDPOINT:
        endpoint = url_for(ENDPOINT, uuid='__UUID__')
        return STYLE + '<script>' + (TAB_JS % {'endpoint': json.dumps(endpoint)}) + '</script>'
    return ''
