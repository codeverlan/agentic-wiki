from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from memwiki.claims import build_claim
from memwiki.html import escape, render_page
from memwiki.ids import sha256_bytes, slugify, stable_id, utc_now
from memwiki.manifest import read_jsonl, write_jsonl
from memwiki.policy import (
    OperationContext,
    append_event,
    client_record_id,
    require_operation_context,
    sensitivity,
)
from memwiki.safe_paths import write_confined_text
from memwiki.sources import register_source
from memwiki.workspace import Workspace

AGENT_SLICE_STATUSES = [
    "active",
    "blocked",
    "waiting-on-dependent",
    "validated",
    "merged",
    "superseded",
]

AGENT_STATUS_LANES = [
    "internal_agent_work",
    "external_capability_activity",
    "validation_evidence",
    "supervision_required",
]

AGENT_STATUS_LANE_LABELS = {
    "internal_agent_work": "Internal agent work",
    "external_capability_activity": "External capability activity",
    "validation_evidence": "Validation/evidence",
    "supervision_required": "Supervision-required items",
}

REQUIRED_QUEUE_FIELDS = [
    "schema_version",
    "generated_by",
    "updated_at",
    "source_spec",
    "source_of_truth",
    "status_values",
    "slices",
]

REQUIRED_SLICE_FIELDS = [
    "id",
    "status",
    "branch",
    "description",
    "dependencies",
    "blocked_by",
    "proposed_by",
    "assigned_to",
    "allowed_paths",
    "forbidden_paths",
    "validation",
    "next_action",
    "updated_at",
]

AGENT_INCIDENT_SEVERITIES = ["info", "warning", "high", "critical"]

AGENT_INCIDENT_CATEGORIES = [
    "stale_lease",
    "repair_loop_exhausted",
    "expected_resource_unavailable",
    "external_call_failed",
    "escalation_step_skipped",
    "safety_or_supervision_stop",
    "whole_run_stop",
    "phi_monitoring_alert",
    "credential_or_secret_exposure_risk",
    "abnormal_behavior",
]

AGENT_INCIDENT_STATUSES = [
    "open",
    "monitoring",
    "contained",
    "resolved",
    "superseded",
    "false_positive",
]

REQUIRED_INCIDENT_LOG_FIELDS = [
    "schema_version",
    "generated_by",
    "updated_at",
    "source_spec",
    "source_of_truth",
    "append_only",
    "incidents",
]

REQUIRED_INCIDENT_FIELDS = [
    "id",
    "timestamp",
    "severity",
    "category",
    "summary",
    "trigger",
    "related_slice",
    "status",
    "evidence",
    "monitoring_phase",
    "next_action",
]

PROHIBITED_INCIDENT_VALUE_FIELDS = {
    "credential_value",
    "ephi_value",
    "password_value",
    "phi_value",
    "raw_ephi",
    "raw_phi",
    "raw_secret",
    "raw_token",
    "secret_value",
    "token_value",
}

DEFAULT_PHI_MONITORING_PHASES = [
    {
        "phase_id": "preflight-security-baseline",
        "cadence": "before unattended continuous run or release",
        "reminder": "Run dependency, secret, static analysis, and access-control checks.",
        "evidence": "scan report path and reviewed access-control notes",
        "user_action": "Confirm baseline security posture before PHI-capable development proceeds.",
    },
    {
        "phase_id": "implementation-monitoring",
        "cadence": "during implementation slices",
        "reminder": "Monitor logs, audit events, and unexpected file or network activity for abnormal behavior.",
        "evidence": "metadata-only audit summary",
        "user_action": "Review possible information, credential, plan, or PHI leakage indicators.",
    },
    {
        "phase_id": "recurring-security-scan",
        "cadence": "monthly or before release",
        "reminder": "Run dependency, secret, static analysis, and access-review checks.",
        "evidence": "scan report path",
        "user_action": "Review abnormal behavior and possible information leakage.",
    },
    {
        "phase_id": "incident-response-review",
        "cadence": "when an incident or suspicious event is recorded",
        "reminder": "Preserve metadata-only evidence and escalate user-supervised response.",
        "evidence": "incident ID, timestamps, hashes, and evidence pointers",
        "user_action": "Decide containment, notification, credential rotation, and follow-up review steps.",
    },
]


@dataclass(frozen=True)
class AgentRunMetadata:
    objective: str = ""
    stop_reason: str = ""
    active_branch: str = ""
    commit: str = ""
    remote_pr: str = ""
    branch_ledger: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class AgentRunStateRenderResult:
    output: str
    queue: str
    slice_count: int
    status_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AgentHandoffDigestMetadata:
    objective: str = ""
    trigger: str = ""
    active_authority: str = ""
    resume_command: str = ""
    resume_path: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class AgentHandoffDigestRenderResult:
    output: str
    queue: str
    trigger: str
    slice_count: int
    status_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AgentIncidentLogRenderResult:
    output: str
    incident_log: str
    incident_count: int
    handles_phi: bool
    hipaa_log_variant: bool
    severity_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AgentRunStateDraftResult:
    draft_id: str
    page_id: str
    queue: str
    source_id: str
    slice_count: int
    status_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _as_list(value: object, field: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"Slice field {field} must be a string or list")


def _required_object(value: object, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _validate_status_lanes(queue: Dict[str, Any]) -> None:
    value = queue.get("status_lanes")
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError("Slice queue status_lanes must be a list")
    for index, item_value in enumerate(value, start=1):
        item = _required_object(item_value, f"Status lane {index}")
        lane_id = str(item.get("lane_id", ""))
        if lane_id not in AGENT_STATUS_LANES:
            raise ValueError(f"Unknown status lane for lane {index}: {lane_id}")
        _as_list(item.get("source_artifacts"), "source_artifacts")


def _validate_worker_lease(slice_item: Dict[str, Any], index: int) -> None:
    value = slice_item.get("worker_lease")
    if value is None:
        return
    lease = _required_object(value, f"Slice {index} worker_lease")
    _as_list(lease.get("assigned_paths"), "worker_lease.assigned_paths")


def _validate_no_prohibited_incident_values(value: object, path: str = "incident_log") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in PROHIBITED_INCIDENT_VALUE_FIELDS:
                raise ValueError(
                    "Incident log must not store PHI, credentials, or secret values; "
                    f"prohibited field: {path}.{key_text}"
                )
            _validate_no_prohibited_incident_values(item, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value, start=1):
            _validate_no_prohibited_incident_values(item, f"{path}[{index}]")


def _validate_monitoring_phases(log: Dict[str, Any]) -> None:
    value = log.get("monitoring_phases")
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError("Incident log monitoring_phases must be a list")
    for index, item_value in enumerate(value, start=1):
        item = _required_object(item_value, f"Monitoring phase {index}")
        if not str(item.get("phase_id", "")):
            raise ValueError(f"Monitoring phase {index} missing required field: phase_id")


def _incident_phi_profile(log: Dict[str, Any]) -> Dict[str, Any]:
    value = log.get("phi_profile")
    if value is None:
        return {
            "handles_phi": False,
            "hipaa_log_variant": False,
            "no_phi_or_secret_values": True,
            "compliance_note": "Metadata-only log; not legal certification.",
            "best_practice_awareness": [],
        }
    return _required_object(value, "Incident log phi_profile")


def _incident_handles_phi(log: Dict[str, Any]) -> bool:
    return bool(_incident_phi_profile(log).get("handles_phi", False))


def _incident_hipaa_log_variant(log: Dict[str, Any]) -> bool:
    return bool(_incident_phi_profile(log).get("hipaa_log_variant", False))


def _incident_no_phi_or_secret_values(log: Dict[str, Any]) -> bool:
    return bool(_incident_phi_profile(log).get("no_phi_or_secret_values", False))


def load_agent_incident_log(incident_log_path: Path | str) -> Dict[str, Any]:
    path = Path(incident_log_path)
    if not path.exists():
        raise ValueError(f"Incident log does not exist: {path}")
    log = _required_object(json.loads(path.read_text(encoding="utf-8")), "Incident log")
    _validate_no_prohibited_incident_values(log)
    for field in REQUIRED_INCIDENT_LOG_FIELDS:
        if field not in log:
            raise ValueError(f"Incident log missing required field: {field}")
    if log.get("source_of_truth") is not True:
        raise ValueError("Incident log must declare source_of_truth: true")
    if log.get("append_only") is not True:
        raise ValueError("Incident log must declare append_only: true")
    incidents = log.get("incidents")
    if not isinstance(incidents, list):
        raise ValueError("Incident log incidents must be a list")
    _incident_phi_profile(log)
    if _incident_handles_phi(log):
        if not _incident_hipaa_log_variant(log):
            raise ValueError("PHI-handling incident logs must declare hipaa_log_variant: true")
        if not _incident_no_phi_or_secret_values(log):
            raise ValueError("PHI-handling incident logs must declare no_phi_or_secret_values: true")
        if not log.get("monitoring_phases"):
            log["monitoring_phases"] = list(DEFAULT_PHI_MONITORING_PHASES)
    _validate_monitoring_phases(log)
    for index, value in enumerate(incidents, start=1):
        item = _required_object(value, f"Incident {index}")
        for field in REQUIRED_INCIDENT_FIELDS:
            if field not in item:
                raise ValueError(f"Incident {index} missing required field: {field}")
        severity = str(item["severity"])
        if severity not in AGENT_INCIDENT_SEVERITIES:
            raise ValueError(f"Unknown incident severity for {item.get('id', f'incident {index}')}: {severity}")
        category = str(item["category"])
        if category not in AGENT_INCIDENT_CATEGORIES:
            raise ValueError(f"Unknown incident category for {item.get('id', f'incident {index}')}: {category}")
        status = str(item["status"])
        if status not in AGENT_INCIDENT_STATUSES:
            raise ValueError(f"Unknown incident status for {item.get('id', f'incident {index}')}: {status}")
        _as_list(item.get("evidence"), "evidence")
    return log


def load_agent_slice_queue(queue_path: Path | str) -> Dict[str, Any]:
    path = Path(queue_path)
    if not path.exists():
        raise ValueError(f"Slice queue does not exist: {path}")
    queue = _required_object(json.loads(path.read_text(encoding="utf-8")), "Slice queue")
    for field in REQUIRED_QUEUE_FIELDS:
        if field not in queue:
            raise ValueError(f"Slice queue missing required field: {field}")
    if queue.get("source_of_truth") is not True:
        raise ValueError("Slice queue must declare source_of_truth: true")
    status_values = queue.get("status_values")
    if not isinstance(status_values, list):
        raise ValueError("Slice queue status_values must be a list")
    unknown_declared = [str(status) for status in status_values if str(status) not in AGENT_SLICE_STATUSES]
    if unknown_declared:
        raise ValueError(f"Unknown declared slice status: {', '.join(unknown_declared)}")
    slices = queue.get("slices")
    if not isinstance(slices, list):
        raise ValueError("Slice queue slices must be a list")
    for index, value in enumerate(slices, start=1):
        item = _required_object(value, f"Slice {index}")
        for field in REQUIRED_SLICE_FIELDS:
            if field not in item:
                raise ValueError(f"Slice {index} missing required field: {field}")
        status = str(item["status"])
        if status not in AGENT_SLICE_STATUSES:
            raise ValueError(f"Unknown slice status for {item.get('id', f'slice {index}')}: {status}")
        for field in ["dependencies", "blocked_by", "allowed_paths", "forbidden_paths", "validation"]:
            _as_list(item.get(field), field)
        _validate_worker_lease(item, index)
    _validate_status_lanes(queue)
    return queue


def agent_slice_status_counts(queue: Dict[str, Any]) -> Dict[str, int]:
    counts = Counter(str(item.get("status", "")) for item in queue.get("slices", []))
    return {status: counts[status] for status in AGENT_SLICE_STATUSES if counts[status]}


def agent_incident_severity_counts(log: Dict[str, Any]) -> Dict[str, int]:
    counts = Counter(str(item.get("severity", "")) for item in log.get("incidents", []))
    return {severity: counts[severity] for severity in AGENT_INCIDENT_SEVERITIES if counts[severity]}


def _definition_row(label: str, value: str) -> str:
    return f"<dt>{escape(label)}</dt><dd>{escape(value) if value else 'Not recorded.'}</dd>"


def _list_text(value: object, field: str) -> str:
    items = _as_list(value, field)
    return ", ".join(items) if items else "None"


def _human_artifacts(queue: Dict[str, Any]) -> Dict[str, str]:
    value = queue.get("human_artifacts")
    if not isinstance(value, dict):
        return {}
    return {str(key): str(path) for key, path in value.items()}


def _human_artifact_rows(queue: Dict[str, Any]) -> str:
    rows = []
    for label, path in sorted(_human_artifacts(queue).items()):
        rows.append(
            f"""
          <tr>
            <td>{escape(label.replace("_", " "))}</td>
            <td><code>{escape(path)}</code></td>
          </tr>"""
        )
    if not rows:
        return """
          <tr>
            <td colspan="2">No human artifacts advertised by the queue.</td>
          </tr>"""
    return "".join(rows)


def _human_artifact_value(queue: Dict[str, Any], key: str) -> str:
    return _human_artifacts(queue).get(key, "")


def _incident_best_practice_rows(log: Dict[str, Any]) -> str:
    phi_profile = _incident_phi_profile(log)
    practices = _as_list(phi_profile.get("best_practice_awareness"), "best_practice_awareness")
    if not practices and _incident_handles_phi(log):
        practices = [
            "least privilege",
            "audit logging",
            "regular security scans",
            "secret scanning",
            "access review",
            "incident response planning",
        ]
    if not practices:
        return "<li>No PHI-specific best-practice awareness items recorded.</li>"
    return "".join(f"<li>{escape(item)}</li>" for item in practices)


def _incident_monitoring_phase_rows(log: Dict[str, Any]) -> str:
    phases = log.get("monitoring_phases")
    if not isinstance(phases, list) or not phases:
        return """
          <tr>
            <td colspan="5">No monitoring phases recorded.</td>
          </tr>"""
    rows = []
    for item_value in phases:
        item = _required_object(item_value, "Monitoring phase")
        rows.append(
            f"""
          <tr>
            <td><code>{escape(str(item.get("phase_id") or ""))}</code></td>
            <td>{escape(str(item.get("cadence") or "Not recorded."))}</td>
            <td>{escape(str(item.get("reminder") or "Not recorded."))}</td>
            <td>{escape(str(item.get("evidence") or "Not recorded."))}</td>
            <td>{escape(str(item.get("user_action") or "Not recorded."))}</td>
          </tr>"""
        )
    return "".join(rows)


def _incident_rows(log: Dict[str, Any]) -> str:
    rows = []
    for item in log.get("incidents", []):
        rows.append(
            f"""
          <tr class="{escape(_status_class(str(item["severity"])))}">
            <td><code>{escape(str(item["id"]))}</code></td>
            <td>{escape(str(item["timestamp"]))}</td>
            <td><span class="status">{escape(str(item["severity"]))}</span></td>
            <td>{escape(str(item["category"]))}</td>
            <td>{escape(str(item["summary"]))}</td>
            <td>{escape(str(item["trigger"]))}</td>
            <td>{escape(str(item["related_slice"]))}</td>
            <td>{escape(str(item["status"]))}</td>
            <td>{escape(_list_text(item.get("evidence"), "evidence"))}</td>
            <td>{escape(str(item["monitoring_phase"]))}</td>
            <td>{escape(str(item["next_action"]))}</td>
          </tr>"""
        )
    if not rows:
        return """
          <tr>
            <td colspan="11">No incidents recorded.</td>
          </tr>"""
    return "".join(rows)


def _status_class(status: str) -> str:
    return "status-" + slugify(status)


def _slice_rows(queue: Dict[str, Any]) -> str:
    rows = []
    for item in queue.get("slices", []):
        status = str(item["status"])
        blocker_parts = []
        dependencies = _list_text(item.get("dependencies"), "dependencies")
        blocked_by = _list_text(item.get("blocked_by"), "blocked_by")
        if dependencies != "None":
            blocker_parts.append(f"Dependencies: {dependencies}")
        if blocked_by != "None":
            blocker_parts.append(f"Blocked by: {blocked_by}")
        dependency_blocker = "; ".join(blocker_parts) if blocker_parts else "None"
        rows.append(
            f"""
          <tr class="{escape(_status_class(status))}">
            <td>{escape(str(item["id"]))}</td>
            <td><span class="status">{escape(status)}</span></td>
            <td><code>{escape(str(item["branch"]))}</code></td>
            <td>{escape(str(item["description"]))}</td>
            <td>{escape(dependency_blocker)}</td>
            <td>{escape(_list_text(item.get("validation"), "validation"))}</td>
            <td>{escape(str(item["next_action"]))}</td>
          </tr>"""
        )
    if not rows:
        return """
          <tr class="status-superseded">
            <td colspan="7">No slices recorded.</td>
          </tr>"""
    return "".join(rows)


def _lease_rows(queue: Dict[str, Any]) -> str:
    rows = []
    for item in queue.get("slices", []):
        lease_value = item.get("worker_lease")
        if not isinstance(lease_value, dict):
            continue
        rows.append(
            f"""
          <tr>
            <td>{escape(str(item["id"]))}</td>
            <td>{escape(str(lease_value.get("lease_id") or "Not recorded."))}</td>
            <td>{escape(str(lease_value.get("worker_id") or item.get("assigned_to") or "Not recorded."))}</td>
            <td><code>{escape(str(lease_value.get("branch") or item.get("branch") or ""))}</code></td>
            <td>{escape(_list_text(lease_value.get("assigned_paths"), "worker_lease.assigned_paths"))}</td>
            <td>{escape(str(lease_value.get("heartbeat_at") or "Not recorded."))}</td>
            <td>{escape(str(lease_value.get("status") or "Not recorded."))}</td>
            <td>{escape(str(lease_value.get("stale_after") or "Not recorded."))}</td>
            <td>{escape(str(lease_value.get("stale_handling") or "Not recorded."))}</td>
            <td>{escape(str(lease_value.get("reassignment_or_supersession") or "Not recorded."))}</td>
          </tr>"""
        )
    if not rows:
        return """
          <tr>
            <td colspan="10">No worker leases recorded.</td>
          </tr>"""
    return "".join(rows)


def _status_lane_rows(queue: Dict[str, Any]) -> str:
    raw_lanes = queue.get("status_lanes")
    if isinstance(raw_lanes, list):
        lanes = [_required_object(value, "Status lane") for value in raw_lanes]
    else:
        lanes = [
            {
                "lane_id": lane_id,
                "label": AGENT_STATUS_LANE_LABELS[lane_id],
                "current_summary": "",
                "blocked_summary": "",
                "completed_summary": "",
                "last_updated": "",
                "source_artifacts": [],
            }
            for lane_id in AGENT_STATUS_LANES
        ]
    rows = []
    for lane in lanes:
        lane_id = str(lane.get("lane_id", ""))
        label = str(lane.get("label") or AGENT_STATUS_LANE_LABELS.get(lane_id, lane_id))
        rows.append(
            f"""
          <tr>
            <td><code>{escape(lane_id)}</code></td>
            <td>{escape(label)}</td>
            <td>{escape(str(lane.get("current_summary") or "Not recorded."))}</td>
            <td>{escape(str(lane.get("blocked_summary") or "Not recorded."))}</td>
            <td>{escape(str(lane.get("completed_summary") or "Not recorded."))}</td>
            <td>{escape(str(lane.get("last_updated") or "Not recorded."))}</td>
            <td>{escape(_list_text(lane.get("source_artifacts"), "source_artifacts"))}</td>
          </tr>"""
        )
    return "".join(rows)


def render_agent_run_state_html(
    queue: Dict[str, Any],
    *,
    page_id: str,
    metadata: AgentRunMetadata,
    source_queue: str,
    generated_at: Optional[str] = None,
) -> str:
    counts = agent_slice_status_counts(queue)
    status_summary = ", ".join(f"{status}: {count}" for status, count in counts.items()) or "No slices"
    queue_updated_at = str(queue.get("updated_at", ""))
    body = f"""
    <section id="run-summary">
      <h2>Run Summary</h2>
      <dl>
        {_definition_row("Objective", metadata.objective)}
        {_definition_row("Stop reason", metadata.stop_reason)}
        {_definition_row("Active branch", metadata.active_branch)}
        {_definition_row("Commit", metadata.commit)}
        {_definition_row("Remote / PR", metadata.remote_pr)}
        {_definition_row("Branch ledger", metadata.branch_ledger)}
        {_definition_row("Queue", source_queue)}
        {_definition_row("Queue updated", queue_updated_at)}
        {_definition_row("Slice status counts", status_summary)}
      </dl>
    </section>
    <section id="status-lanes">
      <h2>Status Lanes</h2>
      <p>Use the combined run summary for top-level status. Use these lanes to separate internal agent
      work, external capability activity, validation/evidence, and supervision-required items.</p>
      <table>
        <thead>
          <tr>
            <th scope="col">Lane ID</th>
            <th scope="col">Lane</th>
            <th scope="col">Current</th>
            <th scope="col">Blocked</th>
            <th scope="col">Completed</th>
            <th scope="col">Last updated</th>
            <th scope="col">Source artifacts</th>
          </tr>
        </thead>
        <tbody>{_status_lane_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="worker-leases">
      <h2>Worker Leases</h2>
      <p>Use leases to detect stale workers, preserve slice-owned work, and reassign or supersede
      stalled slices without blocking independent slices.</p>
      <table>
        <thead>
          <tr>
            <th scope="col">Slice ID</th>
            <th scope="col">Lease ID</th>
            <th scope="col">Worker ID</th>
            <th scope="col">Branch</th>
            <th scope="col">Assigned paths</th>
            <th scope="col">Heartbeat</th>
            <th scope="col">Lease status</th>
            <th scope="col">Stale after</th>
            <th scope="col">Stale handling</th>
            <th scope="col">Reassignment or supersession</th>
          </tr>
        </thead>
        <tbody>{_lease_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="human-artifacts">
      <h2>Human Review Artifacts</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Artifact</th>
            <th scope="col">Path</th>
          </tr>
        </thead>
        <tbody>{_human_artifact_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="slice-state">
      <h2>Slice State</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Slice ID</th>
            <th scope="col">Status</th>
            <th scope="col">Branch</th>
            <th scope="col">Description</th>
            <th scope="col">Dependency/Blocker</th>
            <th scope="col">Validation</th>
            <th scope="col">Next action</th>
          </tr>
        </thead>
        <tbody>{_slice_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="resume">
      <h2>Resume Instructions</h2>
      <ol>
        <li>Load the source queue listed in this page metadata.</li>
        <li>Start with the first <code>active</code> slice that has no unresolved dependency.</li>
        <li>Keep blocked slices recorded and continue independent slices when available.</li>
        <li>Run the validation listed for the slice before promotion, merge, or handoff.</li>
      </ol>
    </section>
"""
    return render_page(
        title="Agent Development Run State",
        page_id=page_id,
        page_type="agent_run_state",
        body=body,
        metadata={
            "memwiki:agentMemoryType": "agent_development_run_state",
            "memwiki:sourceQueue": source_queue,
            "memwiki:sourceSpec": queue.get("source_spec"),
            "memwiki:sourceOfTruth": queue.get("source_of_truth"),
            "memwiki:queueUpdatedAt": queue_updated_at,
            "memwiki:sliceCount": len(queue.get("slices", [])),
            "memwiki:sliceStatusCounts": counts,
            "memwiki:statusLanes": AGENT_STATUS_LANES,
            "memwiki:workerLeases": True,
            "memwiki:humanArtifacts": _human_artifacts(queue),
        },
        generated_at=generated_at,
    )


def render_agent_run_state(
    queue_path: Path | str,
    output_path: Path | str,
    metadata: Optional[AgentRunMetadata] = None,
    output_root: Path | str | None = None,
) -> AgentRunStateRenderResult:
    source = Path(queue_path)
    output = Path(output_path)
    queue = load_agent_slice_queue(source)
    digest = sha256_bytes(source.read_bytes())
    page_id = stable_id("page", "agent-run-state", digest)
    html = render_agent_run_state_html(
        queue,
        page_id=page_id,
        metadata=metadata or AgentRunMetadata(),
        source_queue=str(source),
    )
    output = write_confined_text(output_root or source.parent, output, html)
    return AgentRunStateRenderResult(
        output=str(output),
        queue=str(source),
        slice_count=len(queue.get("slices", [])),
        status_counts=agent_slice_status_counts(queue),
    )


def render_agent_handoff_digest_html(
    queue: Dict[str, Any],
    *,
    page_id: str,
    metadata: AgentHandoffDigestMetadata,
    source_queue: str,
    generated_at: Optional[str] = None,
) -> str:
    counts = agent_slice_status_counts(queue)
    status_summary = ", ".join(f"{status}: {count}" for status, count in counts.items()) or "No slices"
    queue_updated_at = str(queue.get("updated_at", ""))
    resume_command = metadata.resume_command or "Not recorded."
    body = f"""
    <section id="handoff-summary">
      <h2>Handoff Summary</h2>
      <dl>
        {_definition_row("Objective", metadata.objective)}
        {_definition_row("Trigger", metadata.trigger)}
        {_definition_row("Active authority", metadata.active_authority)}
        {_definition_row("Queue", source_queue)}
        {_definition_row("Queue updated", queue_updated_at)}
        {_definition_row("Slice status counts", status_summary)}
      </dl>
    </section>
    <section id="queue-state">
      <h2>Queue State</h2>
      <p>The queue remains the machine-readable source of truth. This digest is a human-readable
      resume surface for stops, compaction risk, stale leases, and user-return checkpoints.</p>
      <table>
        <thead>
          <tr>
            <th scope="col">Lane ID</th>
            <th scope="col">Lane</th>
            <th scope="col">Current</th>
            <th scope="col">Blocked</th>
            <th scope="col">Completed</th>
            <th scope="col">Last updated</th>
            <th scope="col">Source artifacts</th>
          </tr>
        </thead>
        <tbody>{_status_lane_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="worker-leases">
      <h2>Worker Leases</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Slice ID</th>
            <th scope="col">Lease ID</th>
            <th scope="col">Worker ID</th>
            <th scope="col">Branch</th>
            <th scope="col">Assigned paths</th>
            <th scope="col">Heartbeat</th>
            <th scope="col">Lease status</th>
            <th scope="col">Stale after</th>
            <th scope="col">Stale handling</th>
            <th scope="col">Reassignment or supersession</th>
          </tr>
        </thead>
        <tbody>{_lease_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="blockers-validation">
      <h2>Blockers And Validation</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Slice ID</th>
            <th scope="col">Status</th>
            <th scope="col">Branch</th>
            <th scope="col">Description</th>
            <th scope="col">Dependency/Blocker</th>
            <th scope="col">Validation</th>
            <th scope="col">Next action</th>
          </tr>
        </thead>
        <tbody>{_slice_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="external-capabilities">
      <h2>External Capabilities</h2>
      <p>External connections, plugin use, MCP servers, API integrations, and dashboard data remain
      tracked by project-local artifacts. Credentials are not displayed in this digest.</p>
      <table>
        <thead>
          <tr>
            <th scope="col">Artifact</th>
            <th scope="col">Path</th>
          </tr>
        </thead>
        <tbody>{_human_artifact_rows(queue)}
        </tbody>
      </table>
    </section>
    <section id="incident-monitoring">
      <h2>Incident And PHI Monitoring</h2>
      <p>Incident and exception logs are project-local metadata-only artifacts. PHI-aware projects
      use the HIPAA-focused variant and do not store PHI, credentials, tokens, passwords, or raw
      secret values in durable incident evidence.</p>
      <dl>
        {_definition_row("Incident log JSON", _human_artifact_value(queue, "incident_log_json"))}
        {_definition_row("Latest incident log", _human_artifact_value(queue, "latest_incident_log"))}
        {_definition_row("Incident log template", _human_artifact_value(queue, "incident_log_template_html"))}
      </dl>
    </section>
    <section id="supervision-resume">
      <h2>Supervision And Resume</h2>
      <dl>
        {_definition_row("Resume path", metadata.resume_path)}
        {_definition_row("Resume command", resume_command)}
      </dl>
      <ol>
        <li>Inspect the source queue and this digest before resuming work.</li>
        <li>Prioritize supervision-required items after the current task reaches a reasonable stop.</li>
        <li>Continue independent slices when one slice is blocked, stale, or waiting on a dependency.</li>
        <li>Run the validation listed for each slice before promotion, merge, or handoff.</li>
      </ol>
    </section>
"""
    return render_page(
        title="Agent Development Handoff Digest",
        page_id=page_id,
        page_type="agent_handoff_digest",
        body=body,
        metadata={
            "memwiki:agentMemoryType": "agent_development_handoff_digest",
            "memwiki:handoffDigest": True,
            "memwiki:handoffTrigger": metadata.trigger,
            "memwiki:activeAuthority": metadata.active_authority,
            "memwiki:resumeCommand": metadata.resume_command,
            "memwiki:resumePath": metadata.resume_path,
            "memwiki:sourceQueue": source_queue,
            "memwiki:sourceSpec": queue.get("source_spec"),
            "memwiki:sourceOfTruth": queue.get("source_of_truth"),
            "memwiki:queueUpdatedAt": queue_updated_at,
            "memwiki:sliceCount": len(queue.get("slices", [])),
            "memwiki:sliceStatusCounts": counts,
            "memwiki:statusLanes": AGENT_STATUS_LANES,
            "memwiki:workerLeases": True,
            "memwiki:humanArtifacts": _human_artifacts(queue),
        },
        generated_at=generated_at,
    )


def render_agent_handoff_digest(
    queue_path: Path | str,
    output_path: Path | str,
    metadata: Optional[AgentHandoffDigestMetadata] = None,
    output_root: Path | str | None = None,
) -> AgentHandoffDigestRenderResult:
    source = Path(queue_path)
    output = Path(output_path)
    queue = load_agent_slice_queue(source)
    resolved_metadata = metadata or AgentHandoffDigestMetadata()
    digest = sha256_bytes(source.read_bytes())
    page_id = stable_id("page", "agent-handoff-digest", digest, resolved_metadata.trigger)
    html = render_agent_handoff_digest_html(
        queue,
        page_id=page_id,
        metadata=resolved_metadata,
        source_queue=str(source),
    )
    output = write_confined_text(output_root or source.parent, output, html)
    return AgentHandoffDigestRenderResult(
        output=str(output),
        queue=str(source),
        trigger=resolved_metadata.trigger,
        slice_count=len(queue.get("slices", [])),
        status_counts=agent_slice_status_counts(queue),
    )


def render_agent_incident_log_html(
    incident_log: Dict[str, Any],
    *,
    page_id: str,
    source_log: str,
    generated_at: Optional[str] = None,
) -> str:
    counts = agent_incident_severity_counts(incident_log)
    severity_summary = ", ".join(f"{severity}: {count}" for severity, count in counts.items()) or "No incidents"
    updated_at = str(incident_log.get("updated_at", ""))
    phi_profile = _incident_phi_profile(incident_log)
    compliance_note = str(phi_profile.get("compliance_note") or "Metadata-only log; not legal certification.")
    handles_phi = _incident_handles_phi(incident_log)
    hipaa_log_variant = _incident_hipaa_log_variant(incident_log)
    no_phi_or_secret_values = _incident_no_phi_or_secret_values(incident_log)
    body = f"""
    <section id="incident-log-summary">
      <h2>Incident Log Summary</h2>
      <dl>
        {_definition_row("Source log", source_log)}
        {_definition_row("Source spec", str(incident_log.get("source_spec") or ""))}
        {_definition_row("Updated", updated_at)}
        {_definition_row("Incident count", str(len(incident_log.get("incidents", []))))}
        {_definition_row("Severity counts", severity_summary)}
        {_definition_row("Handles PHI", "yes" if handles_phi else "no")}
        {_definition_row("HIPAA log variant", "yes" if hipaa_log_variant else "no")}
        {_definition_row("Append-only", "yes" if incident_log.get("append_only") is True else "no")}
      </dl>
    </section>
    <section id="phi-hipaa-safeguards">
      <h2>PHI/HIPAA Safeguards</h2>
      <p>{escape(compliance_note)}</p>
      <p>This incident log is metadata-only. It records event identifiers, timestamps,
      severity, evidence pointers, and review actions; it must not store PHI, credentials,
      secret values, tokens, passwords, or raw sensitive text.</p>
      <dl>
        {_definition_row("No PHI or secret values", "yes" if no_phi_or_secret_values else "no")}
        {_definition_row("Best-practice scope", "software-development safeguards, not legal certification")}
      </dl>
      <ul>{_incident_best_practice_rows(incident_log)}</ul>
    </section>
    <section id="monitoring-phases">
      <h2>Monitoring Phases</h2>
      <p>For PHI-capable projects, use these phases to remind the user to run regular
      security scans and monitor abnormal behavior that may indicate leakage of information,
      credentials, plans, or PHI.</p>
      <table>
        <thead>
          <tr>
            <th scope="col">Phase ID</th>
            <th scope="col">Cadence</th>
            <th scope="col">Reminder</th>
            <th scope="col">Evidence</th>
            <th scope="col">User action</th>
          </tr>
        </thead>
        <tbody>{_incident_monitoring_phase_rows(incident_log)}
        </tbody>
      </table>
    </section>
    <section id="incident-entries">
      <h2>Incident Entries</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Incident ID</th>
            <th scope="col">Timestamp</th>
            <th scope="col">Severity</th>
            <th scope="col">Category</th>
            <th scope="col">Summary</th>
            <th scope="col">Trigger</th>
            <th scope="col">Related slice</th>
            <th scope="col">Status</th>
            <th scope="col">Evidence</th>
            <th scope="col">Monitoring phase</th>
            <th scope="col">Next action</th>
          </tr>
        </thead>
        <tbody>{_incident_rows(incident_log)}
        </tbody>
      </table>
    </section>
    <section id="append-only-boundary">
      <h2>Append-Only Boundary</h2>
      <p>Accepted entries are append-only. Corrections should be recorded as superseding
      entries instead of rewriting history, so unattended agent work leaves a durable trail
      of abnormal conditions and user-supervision needs.</p>
    </section>
"""
    return render_page(
        title="Agent Development Incident And Exception Log",
        page_id=page_id,
        page_type="agent_incident_log",
        body=body,
        metadata={
            "memwiki:agentMemoryType": "agent_development_incident_log",
            "memwiki:sourceLog": source_log,
            "memwiki:sourceSpec": incident_log.get("source_spec"),
            "memwiki:sourceOfTruth": incident_log.get("source_of_truth"),
            "memwiki:appendOnly": incident_log.get("append_only"),
            "memwiki:incidentCount": len(incident_log.get("incidents", [])),
            "memwiki:severityCounts": counts,
            "memwiki:handlesPHI": handles_phi,
            "memwiki:hipaaLogVariant": hipaa_log_variant,
            "memwiki:noPhiOrSecretValues": no_phi_or_secret_values,
        },
        generated_at=generated_at,
    )


def render_agent_incident_log(
    incident_log_path: Path | str,
    output_path: Path | str,
    output_root: Path | str | None = None,
) -> AgentIncidentLogRenderResult:
    source = Path(incident_log_path)
    output = Path(output_path)
    incident_log = load_agent_incident_log(source)
    digest = sha256_bytes(source.read_bytes())
    page_id = stable_id("page", "agent-incident-log", digest)
    html = render_agent_incident_log_html(
        incident_log,
        page_id=page_id,
        source_log=str(source),
    )
    output = write_confined_text(output_root or source.parent, output, html)
    return AgentIncidentLogRenderResult(
        output=str(output),
        incident_log=str(source),
        incident_count=len(incident_log.get("incidents", [])),
        handles_phi=_incident_handles_phi(incident_log),
        hipaa_log_variant=_incident_hipaa_log_variant(incident_log),
        severity_counts=agent_incident_severity_counts(incident_log),
    )


def _source_for_queue(
    workspace: Workspace,
    queue_path: Path,
    context: Optional[OperationContext],
) -> Dict[str, Any]:
    digest = sha256_bytes(queue_path.read_bytes())
    existing = next(
        (record for record in read_jsonl(workspace.path("manifests/sources.jsonl")) if record.get("sha256") == digest),
        None,
    )
    if existing is not None:
        return existing
    return register_source(workspace, queue_path, source_category="agent_run_queue", context=context)


def draft_agent_run_state(
    workspace: Workspace,
    queue_path: Path | str,
    metadata: Optional[AgentRunMetadata] = None,
    context: Optional[OperationContext] = None,
) -> AgentRunStateDraftResult:
    workspace.require()
    require_operation_context(workspace.config_path, "agent_run_state_draft", context)
    source = Path(queue_path)
    queue = load_agent_slice_queue(source)
    source_record = _source_for_queue(workspace, source, context)
    source_id = str(source_record["source_id"])
    digest = sha256_bytes(source.read_bytes())
    page_id = stable_id("page", "agent-run-state", source_id, digest)
    draft_id = stable_id("draft", "agent-run-state", source_id, digest, utc_now())
    draft_root = workspace.path(f"drafts/{draft_id}")
    wiki_root = draft_root / "wiki"
    manifests_root = draft_root / "manifests"
    wiki_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    rendered = render_agent_run_state_html(
        queue,
        page_id=page_id,
        metadata=metadata or AgentRunMetadata(),
        source_queue=str(source),
    )
    (wiki_root / f"{page_id}.html").write_text(rendered, encoding="utf-8")
    counts = agent_slice_status_counts(queue)
    claim = build_claim(
        source_id,
        "Agent development run queue records "
        f"{len(queue.get('slices', []))} slices with status counts {json.dumps(counts, sort_keys=True)}.",
        "json",
        locator_value="slices",
        sensitivity=sensitivity(workspace.config_path),
        client_record_id=client_record_id(workspace.config_path),
        source_category="agent_run_queue",
    )
    write_jsonl(
        manifests_root / "pages.jsonl",
        [
            {
                "page_id": page_id,
                "title": "Agent Development Run State",
                "slug": "agent-development-run-state",
                "page_type": "agent_run_state",
                "review_status": "draft",
                "html_path": f"{page_id}.html",
                "sensitivity": sensitivity(workspace.config_path),
            }
        ],
    )
    write_jsonl(manifests_root / "claims.jsonl", [claim])
    write_jsonl(
        manifests_root / "links.jsonl",
        [{"from_id": page_id, "to_id": claim["claim_id"], "relationship": "supported_by"}],
    )
    (draft_root / "draft.json").write_text(
        json.dumps(
            {
                "draft_id": draft_id,
                "kind": "agent_run_state",
                "created_at": utc_now(),
                "source_id": source_id,
                "page_id": page_id,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    append_event(
        workspace.root,
        "agent_run_state_draft",
        {
            "draft_id": draft_id,
            "page_id": page_id,
            "source_id": source_id,
            "slices": len(queue.get("slices", [])),
            "status_counts": counts,
        },
        context,
    )
    return AgentRunStateDraftResult(
        draft_id=draft_id,
        page_id=page_id,
        queue=str(source),
        source_id=source_id,
        slice_count=len(queue.get("slices", [])),
        status_counts=counts,
    )
