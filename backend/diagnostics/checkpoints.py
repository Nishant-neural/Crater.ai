"""LangGraph checkpointer factory for the diagnosis loop.

Phase 1 Step 5 (diagnosis audit p.6): ``langgraph`` and
``langgraph-checkpoint-postgres`` own the multi-turn loop state. The
production checkpointer is Postgres — the same Postgres the app
already uses (``settings.database_url``) — via ``PostgresSaver``; when
the app is configured for SQLite (the dev default,
``sqlite:///./crater.db``) the checkpointer is a file-backed
``SqliteSaver`` next to that database, so a restart still resumes the
loop. The factory selects on the URL scheme only; no new store is
invented.

Both savers are context managers (``from_conn_string`` yields the
saver); ``open_checkpointer`` enters one and calls ``setup()`` so the
checkpoint tables exist. ``default_checkpointer`` keeps one saver open
for the process lifetime for the app's default graph — tests pass
their own checkpointer explicitly and must not share it.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from backend.config import settings

_DEFAULT_SAVER = None
_DEFAULT_CTX = None


def _sqlite_checkpoint_path(database_url: str) -> str:
    """File for the SQLite checkpointer, next to the app database.

    ``sqlite:///./crater.db`` -> ``./crater_checkpoints.db``;
    ``sqlite:///:memory:`` stays in-memory.
    """
    path = database_url[len("sqlite:///"):]
    if path == ":memory:":
        return ":memory:"
    stem = Path(path)
    return str(stem.with_name(stem.stem + "_checkpoints" + stem.suffix))


@contextmanager
def open_checkpointer(database_url: str | None = None):
    """Yield a set-up LangGraph checkpointer for ``database_url``.

    Postgres URLs (``postgresql://...``) yield a ``PostgresSaver``;
    anything else yields a file-backed ``SqliteSaver``. The caller
    owns the context — the saver must stay open while the graph that
    uses it is alive.
    """
    url = database_url or settings.database_url
    if url.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(url) as saver:
            saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver

        with SqliteSaver.from_conn_string(_sqlite_checkpoint_path(url)) as saver:
            saver.setup()
            yield saver


def default_checkpointer():
    """The process-lifetime checkpointer for the app's default graph."""
    global _DEFAULT_SAVER, _DEFAULT_CTX
    if _DEFAULT_SAVER is None:
        _DEFAULT_CTX = open_checkpointer()
        _DEFAULT_SAVER = _DEFAULT_CTX.__enter__()
    return _DEFAULT_SAVER
