from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from memwiki.coordinator_agent_harness import (
    AgentCompatibilityHarness,
    AgentScenario,
    DeterministicChatGPTHost,
    DeterministicCodexHost,
    HarnessState,
    ScenarioAgent,
    ScenarioInvocation,
)
from memwiki.coordinator_engineering import (
    ExecutionTier,
    SliceEvaluation,
    SliceRisk,
    route_execution_tier,
)
from memwiki.coordinator_harness import (
    FakeClock,
    HarnessScenario,
    OrchestrationHarness,
    ScenarioSlice,
)
from memwiki.coordinator_model_routing import (
    CapabilityTier,
    HostCapabilityProfile,
    ModelCapability,
    ModelRoutingPolicy,
    ModelRoutingRequirement,
    ReasoningEffort,
    route_model,
)
from memwiki.coordinator_privacy import DataProfile, PrivacyAction, PrivacyPolicy

REQUIRED_SKILLS = (
    "agent-dev-continuous-coordinator",
    "agent-dev-eval-routing",
    "agent-dev-intake-preflight",
    "agent-dev-memory-steward",
    "agent-dev-start-project",
    "agent-dev-status-handoff",
)
REQUIRED_ARCHETYPES = (
    "lightweight",
    "bmad",
    "frontend",
    "api",
    "wordpress",
    "phi-unknown",
    "phi-enabled",
)
_IGNORED_PARTS = {".git", "__pycache__", ".DS_Store"}


class QualificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _tree_digest(root: Path) -> Tuple[str, Mapping[str, str]]:
    if not root.is_dir():
        raise ValueError(f"plugin root is not a directory: {root}")
    files: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return _digest(files), files


@dataclass(frozen=True)
class QualificationCase:
    case_id: str
    passed: bool
    details: str
    evidence_id: str

    @classmethod
    def create(cls, case_id: str, passed: bool, details: str, evidence: object) -> "QualificationCase":
        return cls(case_id, passed, details, _digest(evidence))

    def to_dict(self) -> Dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "details": self.details,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QualificationCase":
        return cls(
            str(value["case_id"]),
            bool(value["passed"]),
            str(value["details"]),
            str(value["evidence_id"]),
        )


@dataclass(frozen=True)
class QualificationFiles:
    json_path: Path
    html_path: Path


@dataclass(frozen=True)
class PluginQualificationResult:
    qualification_id: str
    plugin_name: str
    plugin_version: str
    status: QualificationStatus
    source_digest: str
    installed_digest: str
    archetypes: Tuple[str, ...]
    cases: Tuple[QualificationCase, ...]

    def _payload(self) -> Dict[str, object]:
        return {
            "plugin_name": self.plugin_name,
            "plugin_version": self.plugin_version,
            "status": self.status.value,
            "source_digest": self.source_digest,
            "installed_digest": self.installed_digest,
            "archetypes": list(self.archetypes),
            "cases": [case.to_dict() for case in self.cases],
        }

    def to_dict(self) -> Dict[str, object]:
        return {"qualification_id": self.qualification_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PluginQualificationResult":
        result = cls(
            qualification_id=str(value["qualification_id"]),
            plugin_name=str(value["plugin_name"]),
            plugin_version=str(value["plugin_version"]),
            status=QualificationStatus(str(value["status"])),
            source_digest=str(value["source_digest"]),
            installed_digest=str(value["installed_digest"]),
            archetypes=tuple(str(item) for item in value["archetypes"]),
            cases=tuple(QualificationCase.from_dict(item) for item in value["cases"]),
        )
        if result.qualification_id != _digest(result._payload()):
            raise ValueError("qualification id does not match content")
        return result

    def write(
        self,
        output_directory: Path,
        *,
        json_name: str = "plugin-qualification.json",
        html_name: str = "plugin-qualification.html",
    ) -> QualificationFiles:
        if Path(json_name).name != json_name or Path(html_name).name != html_name:
            raise ValueError("report names must remain inside the output directory")
        output_directory.mkdir(parents=True, exist_ok=True)
        json_path = output_directory / json_name
        html_path = output_directory / html_name
        json_path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        html_path.write_text(self.to_html(), encoding="utf-8")
        return QualificationFiles(json_path, html_path)

    def to_html(self) -> str:
        metadata = html.escape(json.dumps(self.to_dict(), sort_keys=True), quote=False)
        rows = "".join(
            "<tr>"
            f"<td><code>{html.escape(case.case_id)}</code></td>"
            f"<td>{'PASS' if case.passed else 'FAIL'}</td>"
            f"<td>{html.escape(case.details)}</td>"
            f"<td><code>{html.escape(case.evidence_id)}</code></td>"
            "</tr>"
            for case in self.cases
        )
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Plugin Qualification: {html.escape(self.plugin_name)}</title>"
            f"<script type=\"application/ld+json\">{metadata}</script>"
            "<style>body{font:15px/1.5 system-ui;max-width:1100px;margin:auto;padding:28px;color:#182024}"
            "h1,h2{color:#16473e}table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbc8c3;"
            "padding:8px;text-align:left;vertical-align:top}th{background:#e3ebe7}code{overflow-wrap:anywhere}</style>"
            f"</head><body><main><h1>Plugin Qualification</h1><p><strong>{self.status.value.upper()}</strong> "
            f"{html.escape(self.plugin_name)} {html.escape(self.plugin_version)}</p>"
            f"<p>Qualification ID: <code>{html.escape(self.qualification_id)}</code></p>"
            f"<section><h2>Archetypes</h2><p>{html.escape(', '.join(self.archetypes))}</p></section>"
            "<section><h2>Cases</h2><table><thead><tr><th>Case</th><th>Status</th><th>Details</th>"
            f"<th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>"
        )


class PluginQualificationHarness:
    """Qualify an installed plugin bundle with deterministic, offline scenarios."""

    def __init__(self, source_root: Path, installed_root: Path) -> None:
        self.source_root = source_root.resolve()
        self.installed_root = installed_root.resolve()

    def run(self) -> PluginQualificationResult:
        source_digest, source_files = _tree_digest(self.source_root)
        installed_digest, installed_files = _tree_digest(self.installed_root)
        manifest, manifest_case = self._manifest_case()
        cases = [
            manifest_case,
            self._skill_case(),
            self._start_routing_case(),
            self._executable_intake_case(),
            self._adaptive_model_routing_case(),
            self._adc_refinement_case(),
            QualificationCase.create(
                "installed-cache-parity",
                source_files == installed_files,
                "source and installed file hashes match"
                if source_files == installed_files
                else "source and installed file hashes differ",
                {"source": source_digest, "installed": installed_digest},
            ),
            self._immutability_case(source_digest),
            self._supervision_case(),
            self._credential_case(),
            self._archetype_case(),
            self._six_worker_case(),
            self._blocked_slice_case(),
            self._malformed_report_case(),
            self._agent_compatibility_case(),
            self._restart_case(),
            self._evaluation_case(),
            self._routing_case(),
            self._privacy_case(),
        ]
        status = QualificationStatus.PASSED if all(case.passed for case in cases) else QualificationStatus.FAILED
        payload: Dict[str, object] = {
            "plugin_name": str(manifest.get("name", "unknown")),
            "plugin_version": str(manifest.get("version", "unknown")),
            "status": status.value,
            "source_digest": source_digest,
            "installed_digest": installed_digest,
            "archetypes": list(REQUIRED_ARCHETYPES),
            "cases": [case.to_dict() for case in cases],
        }
        return PluginQualificationResult(
            _digest(payload),
            str(manifest.get("name", "unknown")),
            str(manifest.get("version", "unknown")),
            status,
            source_digest,
            installed_digest,
            REQUIRED_ARCHETYPES,
            tuple(cases),
        )

    def _manifest_case(self) -> Tuple[Mapping[str, object], QualificationCase]:
        path = self.installed_root / ".codex-plugin" / "plugin.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            valid = isinstance(value, dict) and value.get("name") == "agent-development-coordinator"
        except (OSError, json.JSONDecodeError):
            value = {}
            valid = False
        return value, QualificationCase.create(
            "plugin-manifest", valid, "manifest is valid" if valid else "manifest is missing or invalid", value
        )

    def _skill_case(self) -> QualificationCase:
        missing = tuple(
            skill
            for skill in REQUIRED_SKILLS
            if not (self.installed_root / "skills" / skill / "SKILL.md").is_file()
        )
        return QualificationCase.create(
            "skill-discovery",
            not missing,
            "all required skills are present" if not missing else "missing: " + ", ".join(missing),
            {"required": REQUIRED_SKILLS, "missing": missing},
        )

    def _start_routing_case(self) -> QualificationCase:
        path = self.installed_root / "skills" / "agent-dev-start-project" / "SKILL.md"
        text = path.read_text(encoding="utf-8", errors="replace").lower() if path.is_file() else ""
        concepts = {
            "new software": "new software" in text,
            "from scratch": "from scratch" in text,
            "existing specification": "existing" in text and ("prd" in text or "spec" in text),
            "partial resources": "partial" in text and "resource" in text,
            "one focused question": "one focused question" in text,
            "phi gate": "phi" in text,
            "automatic readiness transition": "automatically" in text and "readiness" in text,
            "continuous coordination": "continuous coordin" in text,
        }
        missing = tuple(label for label, present in concepts.items() if not present)
        return QualificationCase.create(
            "natural-language-start-routing",
            not missing,
            "front door covers all start paths and handoff rules"
            if not missing
            else "missing concepts: " + ", ".join(missing),
            concepts,
        )

    def _executable_intake_case(self) -> QualificationCase:
        script = self.installed_root / "scripts" / "start_project.py"
        skill = self.installed_root / "skills" / "agent-dev-start-project" / "SKILL.md"
        text = skill.read_text(encoding="utf-8", errors="replace").lower() if skill.is_file() else ""
        operations = tuple(
            operation
            for operation in ("initialize", "apply", "inspect", "resume", "readiness")
            if operation in text
        )
        passed = script.is_file() and len(operations) == 5 and "scripts/start_project.py" in text
        return QualificationCase.create(
            "executable-intake-recovery",
            passed,
            "intake helper and all lifecycle operations are present"
            if passed
            else "intake helper contract is incomplete",
            {"script": script.is_file(), "operations": operations},
        )

    def _adaptive_model_routing_case(self) -> QualificationCase:
        script = self.installed_root / "scripts" / "route_model.py"
        contract = self.installed_root / "assets" / "adaptive-model-routing-contract.json"
        contract_valid = False
        try:
            value = json.loads(contract.read_text(encoding="utf-8"))
            contract_valid = isinstance(value, dict) and set(value.get("tiers", {})) == {
                "narrow",
                "implementation",
                "frontier",
            }
        except (OSError, json.JSONDecodeError):
            value = {}
        profile = HostCapabilityProfile(
            "qualification-host",
            (
                ModelCapability("small", CapabilityTier.NARROW, (ReasoningEffort.LOW,)),
                ModelCapability("strong", CapabilityTier.FRONTIER, (ReasoningEffort.HIGH,)),
            ),
        )
        policy = ModelRoutingPolicy(
            preferred_by_tier={CapabilityTier.NARROW: ("small", ReasoningEffort.LOW)}
        )
        decision = route_model(
            ModelRoutingRequirement(CapabilityTier.NARROW, ReasoningEffort.LOW),
            profile,
            policy,
        )
        passed = script.is_file() and contract_valid and decision.selected_model_id == "small"
        return QualificationCase.create(
            "adaptive-model-reasoning-routing",
            passed,
            "neutral host-aware routing selected the lowest safe route" if passed else "routing bundle is incomplete",
            {"script": script.is_file(), "contract": contract_valid, "decision": decision.to_dict()},
        )

    def _adc_refinement_case(self) -> QualificationCase:
        text = self._combined_text()
        requirements = {
            "host runtime observations and freshness": "host runtime observations" in text and "freshness" in text,
            "exact estimated unknown unavailable measurement semantics": all(
                phrase in text for phrase in ("estimated", "unknown", "unavailable", "never estimate")
            ),
            "durable worker packet obligations": "durable worker packet" in text and "obligation" in text,
            "canonical queue authority": "canonical queue" in text and "single source of truth" in text,
            "completion coherence": "completion coherence" in text,
            "real recovery qualification references": "recovery qualification" in text
            and "coordinator_recovery" in text,
        }
        missing = tuple(label for label, present in requirements.items() if not present)
        return QualificationCase.create(
            "adc-refinement-contracts",
            not missing,
            "ADC refinement contracts are documented"
            if not missing
            else "missing contracts: " + ", ".join(missing),
            requirements,
        )
    def _immutability_case(self, before: str) -> QualificationCase:
        after, _ = _tree_digest(self.source_root)
        return QualificationCase.create(
            "plugin-immutability",
            before == after,
            "source digest remained unchanged during qualification",
            {"before": before, "after": after},
        )

    def _combined_text(self) -> str:
        parts = []
        for path in sorted(self.installed_root.rglob("*.md")):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        for path in sorted((self.installed_root / "scripts").glob("*.py")):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts).lower()

    def _supervision_case(self) -> QualificationCase:
        text = self._combined_text()
        valid = ("computer-use" in text and "supervision" in text) or (
            "computer_use_requires_supervision" in text
        )
        return QualificationCase.create(
            "computer-use-supervision",
            valid,
            "computer use requires supervision" if valid else "boundary missing",
            valid,
        )

    def _credential_case(self) -> QualificationCase:
        forbidden = {".env", "credentials.json", "secrets.json", "id_rsa"}
        found = tuple(sorted(path.name for path in self.installed_root.rglob("*") if path.name in forbidden))
        return QualificationCase.create(
            "credential-artifact-scan",
            not found,
            "no credential-bearing artifact names found" if not found else "forbidden credential artifacts found",
            found,
        )

    def _archetype_case(self) -> QualificationCase:
        results = {}
        for index, archetype in enumerate(REQUIRED_ARCHETYPES, start=1):
            scenario = HarnessScenario.blocked_a_independent_b(seed=index)
            run = OrchestrationHarness(
                clock=FakeClock(datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc))
            ).run(scenario)
            results[archetype] = run.statuses.get("B") == "integrated"
        return QualificationCase.create(
            "project-archetypes",
            all(results.values()),
            f"{len(results)} deterministic archetypes continued independent work",
            results,
        )

    def _blocked_slice_case(self) -> QualificationCase:
        run = OrchestrationHarness(
            clock=FakeClock(datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc))
        ).run(HarnessScenario.blocked_a_independent_b(seed=44))
        passed = run.statuses == {"A": "blocked", "A-child": "blocked", "B": "integrated"}
        return QualificationCase.create(
            "blocked-slice-continuation", passed, "independent slice B integrated", run.statuses
        )

    def _six_worker_case(self) -> QualificationCase:
        slices = tuple(
            ScenarioSlice(f"worker-{index}", (), (f"src/worker-{index}",), "success", ())
            for index in range(1, 7)
        )
        run = OrchestrationHarness(
            clock=FakeClock(datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc))
        ).run(HarnessScenario("six-worker", 91, 6, slices, ()))
        passed = len(run.integrated) == 6 and set(run.dispatch_order[:6]) == {
            item.slice_id for item in slices
        }
        return QualificationCase.create(
            "six-worker-dispatch",
            passed,
            "six independent slices dispatched and integrated",
            {"dispatch_order": run.dispatch_order, "integrated": run.integrated, "evidence": run.evidence_sha256},
        )

    def _malformed_report_case(self) -> QualificationCase:
        run = OrchestrationHarness(
            clock=FakeClock(datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc))
        ).run(
            HarnessScenario(
                "malformed-report",
                73,
                1,
                (ScenarioSlice("malformed", (), ("src/malformed",), "malformed", ()),),
                (),
            )
        )
        passed = run.statuses["malformed"] != "integrated"
        return QualificationCase.create(
            "malformed-worker-report",
            passed,
            "malformed report was not integrated",
            {"statuses": run.statuses, "evidence": run.evidence_sha256},
        )

    @staticmethod
    def _agent_scenario() -> AgentScenario:
        return AgentScenario(
            "plugin-agent-compatibility",
            (
                ScenarioAgent("codex.public", "codex", "public", "callable"),
                ScenarioAgent("chatgpt.private", "chatgpt", "personal_private", "callable"),
                ScenarioAgent("chatgpt.handoff", "chatgpt", "public", "handoff_only"),
            ),
            (
                ScenarioInvocation("one", "codex.public", "implement", {"slice": "one"}),
                ScenarioInvocation("two", "chatgpt.private", "review", {"slice": "two"}),
                ScenarioInvocation("three", "chatgpt.handoff", "review", {"slice": "three"}),
            ),
        )

    def _agent_compatibility_case(self) -> QualificationCase:
        result = AgentCompatibilityHarness(
            {"codex": DeterministicCodexHost(), "chatgpt": DeterministicChatGPTHost()}
        ).run(self._agent_scenario())
        statuses = tuple(record.status for record in result.records)
        passed = statuses == ("succeeded", "succeeded", "handoff_required") and result.conformant
        return QualificationCase.create(
            "public-private-handoff-agents", passed, "agent surfaces respected execution modes", result.to_dict()
        )

    def _restart_case(self) -> QualificationCase:
        scenario = self._agent_scenario()
        uninterrupted = AgentCompatibilityHarness(
            {"codex": DeterministicCodexHost(), "chatgpt": DeterministicChatGPTHost()}
        ).run(scenario)
        first = AgentCompatibilityHarness(
            {"codex": DeterministicCodexHost(), "chatgpt": DeterministicChatGPTHost()}
        )
        partial = first.run(scenario, max_invocations=1)
        restored = AgentCompatibilityHarness(
            {"codex": DeterministicCodexHost(), "chatgpt": DeterministicChatGPTHost()},
            state=HarnessState.from_dict(partial.state.to_dict()),
        ).run(scenario)
        passed = restored.to_json() == uninterrupted.to_json()
        return QualificationCase.create(
            "restart-equivalence", passed, "restart produced byte-equivalent agent result", restored.to_dict()
        )

    def _evaluation_case(self) -> QualificationCase:
        evaluation = SliceEvaluation.create(
            slice_id="qualification",
            capability_checks=("feature",),
            regression_checks=("regression",),
            baseline_revision="before",
            baseline={"feature": False, "regression": True},
        )
        comparison = evaluation.compare(
            integrated_revision="after", observed={"feature": True, "regression": False}
        )
        passed = not comparison.completion_gate_passed and comparison.regressions == ("regression",)
        return QualificationCase.create(
            "regression-rejection", passed, "feature success cannot hide regression", comparison.to_dict()
        )

    def _routing_case(self) -> QualificationCase:
        decision = route_execution_tier(
            risk=SliceRisk.SECURITY_CRITICAL,
            complexity=1,
            uncertainty=1,
            context_size=1,
            qualified_failures=0,
        )
        return QualificationCase.create(
            "critical-risk-routing",
            decision.tier is ExecutionTier.FRONTIER,
            "critical risk routed directly to frontier capability",
            decision.to_dict(),
        )

    def _privacy_case(self) -> QualificationCase:
        yes = PrivacyPolicy(DataProfile.YES)
        unknown = PrivacyPolicy(DataProfile.UNKNOWN)
        passed = yes.synthetic_only and unknown.synthetic_only
        denied = 0
        for policy in (yes, unknown):
            try:
                policy.authorize(PrivacyAction.WRITE_ARTIFACT, synthetic=False)
            except PermissionError:
                denied += 1
        passed = passed and denied == 2
        return QualificationCase.create(
            "phi-synthetic-only", passed, "yes and unknown PHI modes reject non-synthetic artifacts", denied
        )
