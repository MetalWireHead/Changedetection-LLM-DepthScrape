"""changedetection.io plugin hooks (registered through the 'changedetectionio' entry-point group)."""
import pluggy
from loguru import logger

from . import llm_patch, service, web
from .settings import PLUGIN_ID, LinkedContentForm

hookimpl = pluggy.HookimplMarker("changedetectionio")


def _late_setup():
    """Things that need the app to be up: the endpoint and the AI prompt patch. Safe to call repeatedly."""
    try:
        web.register_routes()
    except Exception as e:
        logger.debug(f"linked_content: endpoint not registered yet: {e}")
    try:
        llm_patch.apply()
    except Exception as e:
        logger.warning(f"linked_content: could not extend the AI summary prompt: {e}")


@hookimpl
def plugin_settings_tab():
    return {
        'plugin_id': PLUGIN_ID,
        'tab_label': 'Linked Content',
        'form_class': LinkedContentForm,
    }


@hookimpl
def update_handler_alter(update_handler, watch, datastore):
    service.set_datastore(datastore)
    service.remember_handler(watch, update_handler)
    _late_setup()
    return None


@hookimpl
def update_finalize(update_handler, watch, datastore, processing_exception):
    entry = service.forget_handler(watch)
    if watch is not None and processing_exception is None:
        service.set_datastore(datastore)
        service.after_check(watch, datastore, entry)


@hookimpl
def get_html_head_extras():
    _late_setup()
    try:
        return web.head_extras() or None
    except Exception as e:
        logger.warning(f"linked_content: could not add the Linked Content tab: {e}")
        return None

