from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from memwiki.coordinator_refinement_harness import CoordinatorRefinementHarness


def test_refinement_harness_recovers_without_losing_independent_progress(tmp_path: Path) -> None:
    result = CoordinatorRefinementHarness(tmp_path, now=datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc)).run()

    assert result.event_count == result.replay_revision
    assert result.old_lease.status == "active"
    assert result.replacement_lease.attempt == 2
    assert result.late_admission.preserve is True
    assert result.late_admission.integrable is False
    assert result.late_admission.reason == "superseded lease"
    assert result.current_admission.integrable is True
    assert result.duplicate_report_appended is False
    assert result.independent_slice_reported is True


def test_refinement_harness_refuses_completion_until_recovery_is_coherent(tmp_path: Path) -> None:
    result = CoordinatorRefinementHarness(tmp_path).run(run_id="completion-coherence")

    assert result.refusal.complete is False
    assert "required_work_integrated" in result.refusal.failed_conditions
    assert "queue_drained" in result.refusal.failed_conditions
    assert "mandatory_items_resolved" in result.refusal.failed_conditions
    assert result.completion.complete is True
