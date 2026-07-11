from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from memwiki.coordinator_validation import ValidationCommand, ValidationRunner


def command(code: str, **overrides: object) -> ValidationCommand:
    values: dict[str, object] = {
        "argv": (sys.executable, "-c", code),
        "timeout_seconds": 2.0,
        "max_output_bytes": 4096,
    }
    values.update(overrides)
    return ValidationCommand(**values)  # type: ignore[arg-type]


def test_pass_and_failure_are_typed_and_do_not_claim_completion(tmp_path: Path) -> None:
    runner = ValidationRunner(tmp_path)

    passed = runner.run(command("print('ok')"))
    failed = runner.run(command("import sys; print('bad'); sys.exit(7)"))

    assert passed.outcome == "passed"
    assert passed.exit_code == 0
    assert passed.slice_complete is False
    assert passed.stdout == "ok\n"
    assert failed.outcome == "failed"
    assert failed.exit_code == 7
    assert failed.slice_complete is False
    assert failed.receipt_id != passed.receipt_id


def test_timeout_terminates_process_group_and_produces_typed_evidence(tmp_path: Path) -> None:
    result = ValidationRunner(tmp_path).run(
        command("import time; print('started', flush=True); time.sleep(30)", timeout_seconds=0.1)
    )

    assert result.outcome == "timed_out"
    assert result.timed_out is True
    assert result.signal_number in {signal.SIGTERM, signal.SIGKILL}
    assert result.stdout == "started\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal semantics")
def test_signal_exit_is_distinct_from_normal_failure(tmp_path: Path) -> None:
    result = ValidationRunner(tmp_path).run(
        command("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
    )

    assert result.outcome == "signalled"
    assert result.exit_code is None
    assert result.signal_number == signal.SIGTERM


def test_cancellation_is_typed(tmp_path: Path) -> None:
    cancelled = threading.Event()
    timer = threading.Timer(0.1, cancelled.set)
    timer.start()
    try:
        result = ValidationRunner(tmp_path).run(
            command("import time; time.sleep(30)"), cancelled=cancelled.is_set
        )
    finally:
        timer.cancel()

    assert result.outcome == "cancelled"
    assert result.cancelled is True


def test_runner_uses_argv_without_shell_and_confines_cwd(tmp_path: Path) -> None:
    runner = ValidationRunner(tmp_path)
    marker = tmp_path / "injected"
    result = runner.run(command(f"print('safe'); # ; touch {marker}"))

    assert result.outcome == "passed"
    assert not marker.exists()
    with pytest.raises(ValueError, match="working directory"):
        runner.run(command("pass", cwd="../outside"))
    with pytest.raises(ValueError, match="working directory"):
        runner.run(command("pass", cwd=str(tmp_path.resolve())))


def test_environment_is_explicit_deterministic_and_secret_values_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOST_ONLY_SECRET", "must-not-pass")
    secret = "canary-super-secret"
    script = (
        "import json, os; "
        "print(json.dumps({'host': os.getenv('HOST_ONLY_SECRET'), "
        "'lang': os.getenv('LANG'), 'token': os.getenv('TOKEN')})); "
        "print(os.getenv('TOKEN'), file=__import__('sys').stderr)"
    )
    result = ValidationRunner(tmp_path).run(
        command(script, env={"TOKEN": secret}), secret_values=(secret,)
    )

    assert json.loads(result.stdout) == {"host": None, "lang": "C.UTF-8", "token": "[REDACTED]"}
    assert result.stderr == "[REDACTED]\n"
    assert secret not in json.dumps(result.to_dict(), sort_keys=True)


def test_output_is_byte_bounded_before_decoding_and_hashing(tmp_path: Path) -> None:
    result = ValidationRunner(tmp_path).run(
        command("print('x' * 10000); print('y' * 10000, file=__import__('sys').stderr)", max_output_bytes=64)
    )

    assert len(result.stdout.encode()) <= 64
    assert len(result.stderr.encode()) <= 64
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert len(result.stdout_sha256) == 64
    assert len(result.stderr_sha256) == 64


def test_receipt_is_deterministic_for_same_observation(tmp_path: Path) -> None:
    runner = ValidationRunner(tmp_path)
    spec = command("print('stable')")

    first = runner.run(spec)
    time.sleep(0.01)
    second = runner.run(spec)

    assert first.receipt_id == second.receipt_id
    assert first.evidence == second.evidence
    assert first.duration_ms >= 0
    assert second.duration_ms >= 0


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"argv": ()}, "argv"),
        ({"argv": ("echo bad",)}, "argv"),
        ({"timeout_seconds": 0}, "timeout"),
        ({"max_output_bytes": 0}, "output"),
        ({"env": {"BAD=NAME": "value"}}, "environment"),
    ],
)
def test_invalid_specs_are_rejected(
    tmp_path: Path, overrides: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        ValidationRunner(tmp_path).run(command("pass", **overrides))
