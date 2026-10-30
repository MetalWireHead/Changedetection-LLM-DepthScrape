"""Append the scraped linked content to the prompt of the AI change summary.

changedetection.io has no hook for the summary prompt, so this wraps two functions of
changedetectionio.llm.evaluator once, the first time any plugin hook runs. Both the watch worker (notification
summary) and the diff page 'Summary' button import summarise_change() when they need it, so they get the wrapper.
"""
import functools
import threading

from loguru import logger

from . import service

_local = threading.local()
_applied = False
_apply_lock = threading.Lock()


def apply():
    global _applied
    with _apply_lock:
        if _applied:
            return
        from changedetectionio.llm import evaluator

        original_summarise = evaluator.summarise_change
        original_build_prompt = evaluator.build_change_summary_prompt

        @functools.wraps(original_summarise)
        def summarise_change(watch, datastore, diff, current_snapshot=''):
            _local.block = ''
            try:
                cfg = service.load_settings(datastore)
                # Nothing to add to when AI is not configured, don't scrape for no one
                if cfg['enabled'] and diff.strip() and evaluator._runtime_llm_config(datastore):
                    ctx = service.context_for_summary(watch, current_snapshot)
                    _local.block = service.prompt_block(service.linked_content(watch, ctx, cfg))
            except Exception as e:
                logger.warning(f"linked_content: continuing without linked content in the AI prompt: {e}")
            try:
                return original_summarise(watch, datastore, diff=diff, current_snapshot=current_snapshot)
            finally:
                _local.block = ''

        @functools.wraps(original_build_prompt)
        def build_change_summary_prompt(*args, **kwargs):
            prompt = original_build_prompt(*args, **kwargs)
            block = getattr(_local, 'block', '')
            return prompt + block if block else prompt

        evaluator.summarise_change = summarise_change
        evaluator.build_change_summary_prompt = build_change_summary_prompt
        _applied = True
        logger.info("linked_content: AI change summary prompt will include linked content")
