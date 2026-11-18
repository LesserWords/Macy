"""Engine entrypoint and background worker loop."""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from typing import Any

import psycopg

import engine
from engine import db, observability, pipeline, services, webhooks

DEFAULT_WORKER_POLL_SECONDS: float = 15.0
MACY_WORKER_LOCK_ID: int = 7_622_001
_WORKER_LOCK = threading.Lock()
_WORKER_THREAD: threading.Thread | None = None


def _execute_worker_tick(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None,
    batch_size: int,
) -> dict[str, Any]:
    run_result = pipeline.process_next_queued_run(conn, workspace_id=workspace_id)
    if run_result is not None and run_result.get("status") == "completed":
        services.report(
            conn,
            workspace_id=uuid.UUID(str(run_result["workspace_id"])),
            run_id=uuid.UUID(str(run_result["id"])),
        )
    deliveries = webhooks.deliver_due(
        conn,
        workspace_id=workspace_id,
        batch_size=batch_size,
    )
    return {"run": run_result, "deliveries": deliveries}


def run_worker_tick(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID | None = None,
    batch_size: int = 10,
) -> dict[str, Any]:
    """Process one queued pipeline run (if any), compile report, and deliver due webhooks."""
    if conn is not None:
        return _execute_worker_tick(conn, workspace_id=workspace_id, batch_size=batch_size)
    with db.get_connection() as managed_conn:
        return _execute_worker_tick(managed_conn, workspace_id=workspace_id, batch_size=batch_size)


def run_worker_loop(
    *,
    poll_interval_seconds: float | None = None,
    max_iterations: int | None = None,
) -> None:
    """Continuously poll for queued pipeline runs and outbound webhook deliveries."""
    interval = (
        poll_interval_seconds
        if poll_interval_seconds is not None
        else float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", str(DEFAULT_WORKER_POLL_SECONDS)))
    )
    iterations = 0
    while True:
        try:
            run_worker_tick()
        except Exception as exc:  # noqa: BLE001
            observability.capture_exception(exc)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(interval)


def reset_embedded_worker_state() -> None:
    """Reset embedded worker thread singleton state (for tests)."""
    global _WORKER_THREAD
    with _WORKER_LOCK:
        _WORKER_THREAD = None


def _run_advisory_locked_worker(
    *,
    poll_interval_seconds: float | None = None,
    max_iterations: int | None = None,
) -> None:
    with db.get_connection() as lock_conn:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (MACY_WORKER_LOCK_ID,))
            row = cur.fetchone()
            acquired = bool(row[0]) if row is not None else False
        if not acquired:
            return
        try:
            run_worker_loop(
                poll_interval_seconds=poll_interval_seconds,
                max_iterations=max_iterations,
            )
        except Exception as exc:  # noqa: BLE001
            observability.capture_exception(exc)


def start_embedded_worker() -> bool:
    """Start the supervised background worker daemon once when EMBEDDED_WORKER=true."""
    global _WORKER_THREAD
    enabled = os.getenv("EMBEDDED_WORKER", "").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return False
    with _WORKER_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return False
        thread = threading.Thread(
            target=_run_advisory_locked_worker,
            daemon=True,
            name="macy-embedded-worker",
        )
        thread.start()
        _WORKER_THREAD = thread
        return True


def main(argv: list[str] | None = None) -> int:
    """Run engine CLI entrypoint or worker loop."""
    print(f"Content Intelligence Engine v{engine.__version__}")
    raw_args = sys.argv[1:] if argv is None else argv
    if "--worker" in raw_args:
        observability.ensure_sentry_initialized()
        run_worker_loop()
        return 0
    keep_alive = os.getenv("KEEP_ALIVE", "").lower() in ("1", "true", "yes")
    if keep_alive and "--once" not in raw_args:
        time.sleep(0.01)
    return 0


if __name__ == "__main__":  # pragma: no cover
    if os.getenv("KEEP_ALIVE", "").lower() in ("1", "true", "yes"):
        main(["--once"])
        observability.ensure_sentry_initialized()
        run_worker_loop()
    else:
        raise SystemExit(main())
