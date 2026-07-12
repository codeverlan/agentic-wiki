from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from memwiki import (
    CapabilityTier,
    HostCapabilityProfile,
    ModelCapability,
    ModelRoutingPolicy,
    ModelRoutingRequirement,
    ReasoningEffort,
)

SCRIPT = Path("/Users/tyler-lcsw/plugins/agent-development-coordinator/scripts/route_model.py")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_dev_route_model", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_resolves_project_policy_to_available_host_route(
    tmp_path: Path, capsys: object
) -> None:
    profile = HostCapabilityProfile(
        "local",
        (
            ModelCapability("gpt-5.6-luna", CapabilityTier.NARROW, (ReasoningEffort.LOW,)),
            ModelCapability(
                "gpt-5.6-sol",
                CapabilityTier.FRONTIER,
                (ReasoningEffort.MEDIUM, ReasoningEffort.HIGH, ReasoningEffort.XHIGH),
            ),
        ),
    )
    policy = ModelRoutingPolicy(
        preferred_by_tier={CapabilityTier.FRONTIER: ("gpt-5.6-sol", ReasoningEffort.HIGH)}
    )
    requirement = ModelRoutingRequirement(
        CapabilityTier.NARROW,
        ReasoningEffort.LOW,
        critical_risk=True,
    )
    paths = {}
    for name, value in (("profile", profile), ("policy", policy), ("requirement", requirement)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value.to_dict()), encoding="utf-8")
        paths[name] = path

    result = _module().main(
        [
            "--host-profile",
            str(paths["profile"]),
            "--policy",
            str(paths["policy"]),
            "--requirement",
            str(paths["requirement"]),
        ]
    )

    assert result == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    decision = json.loads(captured.out)
    assert decision["selected_model_id"] == "gpt-5.6-sol"
    assert decision["selected_reasoning"] == "high"
