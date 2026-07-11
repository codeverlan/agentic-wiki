from memwiki.coordinator_control import ControlSignal, reconcile_control


def test_status_during_active_work_is_read_only() -> None:
    decision = reconcile_control(
        ControlSignal.STATUS,
        active_slice_ids=("AC-1",),
        pending_supervision=2,
    )
    assert decision.cancel_slice_ids == ()
    assert decision.present_supervision is False
    assert decision.stop_requested is False
    assert decision.status_only is True


def test_user_return_waits_for_safe_boundary_without_cancelling_work() -> None:
    active = reconcile_control(
        ControlSignal.USER_RETURN,
        active_slice_ids=("AC-1",),
        pending_supervision=2,
    )
    drained = reconcile_control(
        ControlSignal.USER_RETURN,
        active_slice_ids=(),
        pending_supervision=2,
    )
    assert active.cancel_slice_ids == ()
    assert active.present_supervision is False
    assert active.wake_runner is True
    assert drained.present_supervision is True


def test_late_valid_report_remains_admissible_after_user_return() -> None:
    decision = reconcile_control(
        ControlSignal.USER_RETURN,
        active_slice_ids=("AC-1",),
        pending_supervision=1,
        late_report_slice_id="AC-1",
        late_report_valid=True,
    )
    assert decision.admit_late_report is True
    assert decision.cancel_slice_ids == ()


def test_explicit_stop_is_distinct_from_status_and_return() -> None:
    decision = reconcile_control(
        ControlSignal.STOP,
        active_slice_ids=("AC-1", "AC-2"),
        pending_supervision=1,
    )
    assert decision.stop_requested is True
    assert decision.drain_to_safe_boundary is True
    assert decision.cancel_slice_ids == ()
    assert decision.present_supervision is False


def test_explicit_stop_after_drain_presents_pending_supervision() -> None:
    decision = reconcile_control(
        ControlSignal.STOP,
        active_slice_ids=(),
        pending_supervision=1,
    )
    assert decision.stop_requested is True
    assert decision.present_supervision is True
    assert decision.drain_to_safe_boundary is False
