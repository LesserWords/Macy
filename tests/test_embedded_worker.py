"""Tests for M7-02 D3 embedded worker supervisor with PostgreSQL advisory lock."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


def test_start_embedded_worker_disabled_by_default(monkeypatch: Any) -> None:
    """D3: Embedded worker does not start unless EMBEDDED_WORKER=true."""
    from engine.__main__ import reset_embedded_worker_state, start_embedded_worker

    reset_embedded_worker_state()
    monkeypatch.delenv("EMBEDDED_WORKER", raising=False)
    assert start_embedded_worker() is False


def test_start_embedded_worker_idempotent_and_advisory_locked(monkeypatch: Any) -> None:
    """D3: Embedded worker starts once across reruns and acquires pg_try_advisory_lock."""
    from engine.__main__ import (
        _run_advisory_locked_worker,
        reset_embedded_worker_state,
        start_embedded_worker,
    )

    reset_embedded_worker_state()
    monkeypatch.setenv("EMBEDDED_WORKER", "true")

    fake_thread = MagicMock()
    fake_thread.is_alive.return_value = True
    with patch("engine.__main__.threading.Thread", return_value=fake_thread) as mock_thread:
        assert start_embedded_worker() is True
        # Subsequent Streamlit rerun sees alive thread and does not spawn a second one
        assert start_embedded_worker() is False
        mock_thread.assert_called_once()
        fake_thread.start.assert_called_once()

    reset_embedded_worker_state()

    # Advisory lock denied -> worker loop not called
    mock_conn_denied = MagicMock()
    mock_conn_denied.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
    with (
        patch("engine.__main__.db.get_connection") as mock_get_conn,
        patch("engine.__main__.run_worker_loop") as mock_loop,
    ):
        mock_get_conn.return_value.__enter__.return_value = mock_conn_denied
        _run_advisory_locked_worker(poll_interval_seconds=0.0, max_iterations=1)
        mock_loop.assert_not_called()

    # Advisory lock acquired -> worker loop runs and captures exceptions safely
    mock_conn_ok = MagicMock()
    mock_conn_ok.cursor.return_value.__enter__.return_value.fetchone.return_value = (True,)
    with (
        patch("engine.__main__.db.get_connection") as mock_get_conn,
        patch("engine.__main__.run_worker_loop", side_effect=RuntimeError("boom")) as mock_loop,
        patch("engine.__main__.observability.capture_exception") as mock_cap,
    ):
        mock_get_conn.return_value.__enter__.return_value = mock_conn_ok
        _run_advisory_locked_worker(poll_interval_seconds=0.0, max_iterations=1)
        mock_loop.assert_called_once_with(poll_interval_seconds=0.0, max_iterations=1)
        mock_cap.assert_called_once()
