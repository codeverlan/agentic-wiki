from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


class EvaluationResult(str, Enum):
    IMPROVED = "improved"
    PASSED = "passed"
    NO_CHANGE = "no_change"
    REGRESSED = "regressed"


class ExecutionTier(str, Enum):
    NARROW = "narrow"
    IMPLEMENTATION = "implementation"
    FRONTIER = "frontier"


class SliceRisk(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    SECURITY_CRITICAL = "security_critical"
    PRIVACY_CRITICAL = "privacy_critical"


_SENSITIVE_FRAGMENTS = ("password", "secret", "token", "api_key", "credential")


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _nonempty(values: Sequence[str], label: str) -> Tuple[str, ...]:
    result = tuple(values)
    if any(not item.strip() for item in result) or len(set(result)) != len(result):
        raise ValueError(f"{label} must be non-empty and unique")
    return result


def _reject_sensitive(value: object, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS):
                raise ValueError(f"sensitive metadata is not allowed at {path}.{key}")
            _reject_sensitive(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_sensitive(item, f"{path}[{index}]")


@dataclass(frozen=True)
class EvaluationComparison:
    comparison_id: str
    slice_id: str
    baseline_revision: str
    integrated_revision: str
    status: EvaluationResult
    completion_gate_passed: bool
    newly_passing: Tuple[str, ...]
    regressions: Tuple[str, ...]
    unchanged_failures: Tuple[str, ...]

    def _payload(self) -> Dict[str, object]:
        return {
            "slice_id": self.slice_id,
            "baseline_revision": self.baseline_revision,
            "integrated_revision": self.integrated_revision,
            "status": self.status.value,
            "completion_gate_passed": self.completion_gate_passed,
            "newly_passing": list(self.newly_passing),
            "regressions": list(self.regressions),
            "unchanged_failures": list(self.unchanged_failures),
        }

    def to_dict(self) -> Dict[str, object]:
        return {"comparison_id": self.comparison_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluationComparison":
        result = cls(
            comparison_id=str(value["comparison_id"]),
            slice_id=str(value["slice_id"]),
            baseline_revision=str(value["baseline_revision"]),
            integrated_revision=str(value["integrated_revision"]),
            status=EvaluationResult(str(value["status"])),
            completion_gate_passed=bool(value["completion_gate_passed"]),
            newly_passing=tuple(str(item) for item in value["newly_passing"]),
            regressions=tuple(str(item) for item in value["regressions"]),
            unchanged_failures=tuple(str(item) for item in value["unchanged_failures"]),
        )
        if result.comparison_id != _digest(result._payload()):
            raise ValueError("evaluation comparison id does not match content")
        return result


@dataclass(frozen=True)
class SliceEvaluation:
    evaluation_id: str
    slice_id: str
    capability_checks: Tuple[str, ...]
    regression_checks: Tuple[str, ...]
    baseline_revision: str
    baseline: Mapping[str, bool]
    metadata: Mapping[str, object]

    def _payload(self) -> Dict[str, object]:
        return {
            "slice_id": self.slice_id,
            "capability_checks": list(self.capability_checks),
            "regression_checks": list(self.regression_checks),
            "baseline_revision": self.baseline_revision,
            "baseline": dict(sorted(self.baseline.items())),
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> Dict[str, object]:
        return {"evaluation_id": self.evaluation_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SliceEvaluation":
        created = cls.create(
            slice_id=str(value["slice_id"]),
            capability_checks=tuple(str(item) for item in value["capability_checks"]),
            regression_checks=tuple(str(item) for item in value["regression_checks"]),
            baseline_revision=str(value["baseline_revision"]),
            baseline={str(key): item for key, item in dict(value["baseline"]).items()},
            metadata=dict(value["metadata"]),
        )
        if created.evaluation_id != value["evaluation_id"]:
            raise ValueError("slice evaluation id does not match content")
        return created

    @classmethod
    def create(
        cls,
        *,
        slice_id: str,
        capability_checks: Sequence[str],
        regression_checks: Sequence[str],
        baseline_revision: str,
        baseline: Mapping[str, bool],
        metadata: Optional[Mapping[str, object]] = None,
    ) -> "SliceEvaluation":
        capability = _nonempty(capability_checks, "capability checks")
        regression = _nonempty(regression_checks, "regression checks")
        checks = (*capability, *regression)
        if not slice_id or not baseline_revision:
            raise ValueError("slice id and baseline revision are required")
        if not capability:
            raise ValueError("at least one capability check is required")
        if len(set(checks)) != len(checks):
            raise ValueError("evaluation checks must be unique")
        if set(baseline) != set(checks) or any(type(item) is not bool for item in baseline.values()):
            raise ValueError("baseline evidence must contain one boolean result per check")
        safe_metadata = dict(metadata or {})
        _reject_sensitive(safe_metadata)
        payload: Dict[str, object] = {
            "slice_id": slice_id,
            "capability_checks": list(capability),
            "regression_checks": list(regression),
            "baseline_revision": baseline_revision,
            "baseline": dict(sorted(baseline.items())),
            "metadata": safe_metadata,
        }
        return cls(
            _digest(payload),
            slice_id,
            capability,
            regression,
            baseline_revision,
            dict(baseline),
            safe_metadata,
        )

    def compare(
        self, *, integrated_revision: str, observed: Mapping[str, bool]
    ) -> EvaluationComparison:
        checks = (*self.capability_checks, *self.regression_checks)
        if not integrated_revision or integrated_revision == self.baseline_revision:
            raise ValueError("integrated revision must identify a later revision")
        if set(observed) != set(checks) or any(type(item) is not bool for item in observed.values()):
            raise ValueError("observed evidence must contain one boolean result per check")
        newly = tuple(sorted(check for check in checks if not self.baseline[check] and observed[check]))
        regressions = tuple(sorted(check for check in checks if self.baseline[check] and not observed[check]))
        unchanged = tuple(sorted(check for check in checks if not self.baseline[check] and not observed[check]))
        capability_passed = all(observed[check] for check in self.capability_checks)
        regression_passed = all(observed[check] for check in self.regression_checks)
        gate = capability_passed and regression_passed
        if regressions:
            status = EvaluationResult.REGRESSED
        elif newly:
            status = EvaluationResult.IMPROVED
        elif gate:
            status = EvaluationResult.PASSED
        else:
            status = EvaluationResult.NO_CHANGE
        payload: Dict[str, object] = {
            "slice_id": self.slice_id,
            "baseline_revision": self.baseline_revision,
            "integrated_revision": integrated_revision,
            "status": status.value,
            "completion_gate_passed": gate,
            "newly_passing": list(newly),
            "regressions": list(regressions),
            "unchanged_failures": list(unchanged),
        }
        return EvaluationComparison(
            _digest(payload),
            self.slice_id,
            self.baseline_revision,
            integrated_revision,
            status,
            gate,
            newly,
            regressions,
            unchanged,
        )


@dataclass(frozen=True)
class SliceDefinition:
    slice_id: str
    objective: str
    dominant_risks: Tuple[str, ...]
    verification: Tuple[str, ...]
    done_condition: str
    estimated_minutes: Optional[int] = None


@dataclass(frozen=True)
class DecompositionAdvice:
    may_proceed: bool
    split_recommended: bool
    reasons: Tuple[str, ...]


def advise_decomposition(definition: SliceDefinition) -> DecompositionAdvice:
    reasons = []
    if len(definition.dominant_risks) != 1:
        reasons.append("multiple dominant risks" if definition.dominant_risks else "dominant risk is missing")
    if not definition.verification:
        reasons.append("independent verification is missing")
    if not definition.done_condition.strip():
        reasons.append("clear done condition is missing")
    if definition.estimated_minutes is not None and definition.estimated_minutes <= 0:
        raise ValueError("estimated minutes must be positive")
    return DecompositionAdvice(True, bool(reasons), tuple(reasons))


@dataclass(frozen=True)
class ExecutionTierDecision:
    tier: ExecutionTier
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {"tier": self.tier.value, "reasons": list(self.reasons)}


def route_execution_tier(
    *, risk: SliceRisk, complexity: int, uncertainty: int, context_size: int, qualified_failures: int
) -> ExecutionTierDecision:
    for label, value in (("complexity", complexity), ("uncertainty", uncertainty), ("context size", context_size)):
        if not 1 <= value <= 5:
            raise ValueError(f"{label} must be between one and five")
    if qualified_failures < 0:
        raise ValueError("qualified failures cannot be negative")
    critical = risk in {SliceRisk.SECURITY_CRITICAL, SliceRisk.PRIVACY_CRITICAL}
    score = complexity + uncertainty + context_size
    reasons = []
    if critical:
        tier = ExecutionTier.FRONTIER
        reasons.append(f"{risk.value} work requires the strongest available reasoning tier")
    elif risk is SliceRisk.HIGH or score >= 11 or qualified_failures >= 3:
        tier = ExecutionTier.FRONTIER
        reasons.append("risk, reasoning load, or failure evidence requires frontier capability")
    elif risk is SliceRisk.MODERATE or score >= 7 or qualified_failures >= 1:
        tier = ExecutionTier.IMPLEMENTATION
        reasons.append("implementation reasoning is required")
    else:
        tier = ExecutionTier.NARROW
        reasons.append("bounded low-risk work fits a narrow capability tier")
    if qualified_failures:
        reasons.append(f"escalated using evidence from {qualified_failures} qualified failures")
    return ExecutionTierDecision(tier, tuple(reasons))
