"""Unit tests for `dcs_mission_creator.core.log.configure`.

Monkey-patches `structlog.configure` so the global structlog config is never
actually mutated by the test suite.
"""

from __future__ import annotations

import logging

import pytest
import structlog

from dcs_mission_creator.core import log as log_mod


@pytest.fixture(autouse=True)
def _reset_configured():
    """Force a fresh `_CONFIGURED` flag per test."""
    log_mod._CONFIGURED = False
    yield
    log_mod._CONFIGURED = False


def test_configure_is_idempotent(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(structlog, "configure", lambda **kw: calls.append(kw))
    log_mod.configure()
    log_mod.configure()
    log_mod.configure(verbose=True)
    assert len(calls) == 1


def test_configure_sets_level_info_by_default(monkeypatch):
    captured: dict = {}
    real_make = structlog.make_filtering_bound_logger

    def fake_make(level):
        captured["level"] = level
        return real_make(level)

    monkeypatch.setattr(structlog, "configure", lambda **kw: None)
    monkeypatch.setattr(structlog, "make_filtering_bound_logger", fake_make)
    log_mod.configure(verbose=False)
    assert captured["level"] == logging.INFO


def test_configure_verbose_uses_debug(monkeypatch):
    captured: dict = {}
    real_make = structlog.make_filtering_bound_logger

    def fake_make(level):
        captured["level"] = level
        return real_make(level)

    monkeypatch.setattr(structlog, "configure", lambda **kw: None)
    monkeypatch.setattr(structlog, "make_filtering_bound_logger", fake_make)
    log_mod.configure(verbose=True)
    assert captured["level"] == logging.DEBUG


def test_configure_marks_module_configured(monkeypatch):
    monkeypatch.setattr(structlog, "configure", lambda **kw: None)
    assert log_mod._CONFIGURED is False
    log_mod.configure()
    assert log_mod._CONFIGURED is True
