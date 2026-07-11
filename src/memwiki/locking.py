from __future__ import annotations

from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_SH, flock
from typing import Iterator

from memwiki.workspace import Workspace


@contextmanager
def workspace_lock(workspace: Workspace, *, exclusive: bool) -> Iterator[None]:
    lock_path = workspace.path(".memwiki/promote.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_handle:
        flock(lock_handle.fileno(), LOCK_EX if exclusive else LOCK_SH)
        yield
