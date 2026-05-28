from __future__ import annotations

from typing import Any, Dict, List

from memwiki.ids import stable_id, utc_now


def build_claim(source_id: str, text: str, locator_type: str) -> Dict[str, Any]:
    claim_text = f"Source {source_id} states: {text.strip()}"
    return {
        "claim_id": stable_id("claim", source_id, claim_text),
        "text": claim_text,
        "source_id": source_id,
        "confidence": 0.75,
        "review_status": "draft",
        "contradicts": [],
        "provenance": {
            "generated_at": utc_now(),
            "model_adapter": "dry-run",
            "source_locator": {
                "type": locator_type,
                "value": "extracted/text.txt",
            },
        },
    }


def validate_claim(record: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ["claim_id", "text", "source_id", "confidence", "review_status", "provenance"]:
        if key not in record:
            errors.append(f"claim missing {key}")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        errors.append(f"claim {record.get('claim_id', '<unknown>')} provenance is not an object")
        return errors
    for key in ["generated_at", "model_adapter", "source_locator"]:
        if key not in provenance:
            errors.append(f"claim {record.get('claim_id', '<unknown>')} provenance missing {key}")
    return errors
