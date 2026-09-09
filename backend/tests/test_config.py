"""Tests for configuration parsing."""
from __future__ import annotations

import os
from unittest import mock


def test_default_config_values():
    from vantaflight.config import (
        STREAM_HZ, CORS_ORIGINS, DEFAULT_ADAPTER,
        PX4_SITL_URL, PX4_CONNECTION_TIMEOUT, TELEMETRY_STALE_TIMEOUT,
    )
    assert STREAM_HZ == 10.0
    assert "http://127.0.0.1:5173" in CORS_ORIGINS
    assert "http://localhost:5173" in CORS_ORIGINS
    assert DEFAULT_ADAPTER == "mock"
    assert PX4_SITL_URL == "udpin://0.0.0.0:14540"
    assert PX4_CONNECTION_TIMEOUT == 15.0
    assert TELEMETRY_STALE_TIMEOUT == 3.0


def test_cors_csv_parsing():
    from vantaflight.config import _csv
    assert _csv("a,b,c") == ["a", "b", "c"]
    assert _csv(" a , b ") == ["a", "b"]
    assert _csv("") == []
    assert _csv("single") == ["single"]
