from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class ControlSignal(str, Enum):
    STATUS = "status"
    USER_RETURN = "user_return"
    STOP = "stop"


@dataclass(frozen=True)
class ControlDecision:
    signal: ControlSignal
    status_only: bool
    wake_runner: bool
    stop_requested: bool
    drain_to_safe_boundary: bool
    present_supervision: bool
    cancel_slice_ids: Tuple[str, ...]
    admit_late_report: bool


def reconcile_control(
    signal: ControlSignal,
    *,
    active_slice_ids: Tuple[str, ...],
    pending_supervision: int,
    late_report_slice_id: Optional[str] = None,
    late_report_valid: bool = False,
) -> ControlDecision:
    """Derive control behavior without mutating or cancelling active slice work."""
    if pending_supervision < 0:
        raise ValueError("pending_supervision cannot be negative")
    if late_report_slice_id is not None and not late_report_slice_id:
        raise ValueError("late_report_slice_id cannot be empty")
    active = tuple(sorted(set(active_slice_ids)))
    safe_boundary = not active
    returning = signal is ControlSignal.USER_RETURN
    stopping = signal is ControlSignal.STOP
    return ControlDecision(
        signal=signal,
        status_only=signal is ControlSignal.STATUS,
        wake_runner=returning or stopping,
        stop_requested=stopping,
        drain_to_safe_boundary=stopping and not safe_boundary,
        present_supervision=pending_supervision > 0 and safe_boundary and (returning or stopping),
        cancel_slice_ids=(),
        admit_late_report=bool(
            late_report_valid
            and late_report_slice_id is not None
            and late_report_slice_id in active
        ),
    )
