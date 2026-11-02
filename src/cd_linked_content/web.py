"""The 'Linked Content' tab of the diff page: a JSON endpoint plus the JavaScript that adds the tab.

The diff page's tabs are fixed in its template and there is no hook for them, so the tab and its pane are added
by a script injected through get_html_head_extras().
"""
import time
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from . import service

ENDPOINT = 'linked_content_tab'

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='linked-content')
_jobs = {}
_registered = False

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
      body.textContent = 'Scraping the new links of this change...';
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


def _run_job(key, uuid, from_version, to_version):
    try:
        datastore = service.get_datastore()
        watch = datastore.data['watching'][uuid]
        cfg = service.load_settings(datastore)
        if not cfg['enabled']:
            payload = {'links': [], 'source': cfg['link_source'], 'method': cfg['scrape_method'], 'found': 0}
        else:
            ctx = service.context_for_versions(watch, from_version, to_version)
            payload = service.linked_content(watch, ctx, cfg)
        _jobs[key] = {'status': 'done', 'html': str(service.tab_html(payload, cfg)), 'at': time.time()}
    except Exception as e:
        logger.exception(f"linked_content: tab job failed for {uuid}")
        _jobs[key] = {'status': 'error', 'error': str(e), 'at': time.time()}


def register_routes():
    """Add the endpoint to the Flask app. Safe to call from any hook, any number of times.

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
    _registered = True
    logger.info("linked_content: registered /linked-content endpoint")
    return True


def head_extras():
    """<script> for every page, '' unless the endpoint exists. Runs inside a request, so url_for works."""
    import json
    from flask import url_for

    if not _registered:
        return ''
    endpoint = url_for(ENDPOINT, uuid='__UUID__')
    return '<script>' + (TAB_JS % {'endpoint': json.dumps(endpoint)}) + '</script>'
