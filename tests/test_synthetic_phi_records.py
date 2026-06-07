import json
import subprocess
import sys
from pathlib import Path


def test_synthetic_phi_generator_emits_requested_record_count(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "records"
    completed = subprocess.run(
        [
            sys.executable,
            "fixtures/synthetic_phi/generate_records.py",
            "--count",
            "3",
            "--output-dir",
            str(output_dir),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    files = sorted(output_dir.glob("client-synthetic-*.json"))
    assert len(files) == 3
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["record_policy"]["synthetic"] is True
    assert payload["dataset_metadata"]["record_count"] == 1
    assert payload["client_record"]["record_id"] == "client-synthetic-001"


def test_repo_includes_300_complete_synthetic_phi_record_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    records_dir = repo_root / "fixtures/synthetic_phi/records"
    files = sorted(records_dir.glob("client-synthetic-*.json"))

    assert len(files) == 300
    age_bands = set()
    scenario_ids = set()

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["template_id"] == "synthetic-phi-client-record-template-v1"
        assert payload["dataset_metadata"]["record_count"] == 1
        assert payload["record_policy"]["synthetic"] is True
        record = payload["client_record"]
        required_fields = record["validation_expectations"]["required_top_level_fields"]
        for field in required_fields:
            assert field in record, f"{path.name}: missing {field}"
        assert record["synthetic"] is True
        assert record["sensitivity"] == "synthetic"
        assert record["initial_evaluation"]["provenance"]["claim_scope"] == "initial_evaluation"
        assert record["treatment_plan"]["provenance"]["claim_scope"] == "treatment_plan"
        assert record["treatment_plan"]["smart_goals"]
        assert record["session_notes"]
        assert all(note["provenance"]["claim_scope"] == "session_note" for note in record["session_notes"])
        assert all(note["plan"] for note in record["session_notes"])
        age_bands.add(record["age_band"])
        scenario_ids.add(record["record_provenance"]["scenario_id"])

    assert age_bands == {"child", "adolescent", "young_adult", "adult", "older_adult"}
    assert scenario_ids == {
        "anxiety_related",
        "mood_related",
        "trauma_related",
        "attention_or_executive_functioning",
        "autism_or_neurodevelopmental_support_needs",
        "adjustment_or_life_transition",
        "grief_or_loss",
        "substance_use_related",
        "psychosis_spectrum_related",
        "eating_or_body_image_related",
        "relationship_or_family_stress",
        "school_or_work_functioning",
        "caregiver_stress",
    }
