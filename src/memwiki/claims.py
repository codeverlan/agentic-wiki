from __future__ import annotations

from typing import Any, Dict, List, Optional

from memwiki.ids import stable_id, utc_now


def build_claim(
    source_id: str,
    text: str,
    locator_type: str,
    *,
    locator_value: str = "extracted/text.txt",
    sensitivity: str = "general",
    client_record_id: Optional[str] = None,
    source_category: Optional[str] = None,
    clinical_claim_type: Optional[str] = None,
) -> Dict[str, Any]:
    claim_text = f"Source {source_id} states: {text.strip()}"
    claim: Dict[str, Any] = {
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
                "value": locator_value,
            },
        },
    }
    if sensitivity != "general":
        claim["sensitivity"] = sensitivity
    if client_record_id:
        claim["client_record_id"] = client_record_id
    if source_category:
        claim["source_category"] = source_category
    if clinical_claim_type:
        claim["clinical_claim_type"] = clinical_claim_type
    return claim


def build_clinical_guidance_claim(
    source_id: str,
    fact_claim: Dict[str, Any],
    *,
    client_record_id: str,
    source_category: str,
) -> Dict[str, Any]:
    fact_id = str(fact_claim["claim_id"])
    guidance_text = (
        "Clinical guidance for clinician review: use the cited source fact to consider care planning, "
        "risk review, follow-up questions, and documentation updates. This is decision support, not an "
        "autonomous diagnosis or treatment directive."
    )
    return {
        "claim_id": stable_id("claim", source_id, fact_id, "clinical-guidance"),
        "text": guidance_text,
        "source_id": source_id,
        "confidence": 0.55,
        "review_status": "needs_review",
        "contradicts": [],
        "sensitivity": "phi",
        "client_record_id": client_record_id,
        "source_category": source_category,
        "clinical_claim_type": "clinical_guidance",
        "guidance_type": "decision_support",
        "cited_claim_ids": [fact_id],
        "provenance": {
            "generated_at": utc_now(),
            "model_adapter": "dry-run",
            "source_locator": {
                "type": "derived_claim",
                "value": fact_id,
            },
            "derived_from_claim_ids": [fact_id],
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
    if record.get("clinical_claim_type") == "clinical_guidance":
        if not record.get("guidance_type"):
            errors.append(f"clinical guidance {record.get('claim_id', '<unknown>')} missing guidance_type")
        cited_claim_ids = record.get("cited_claim_ids")
        if not isinstance(cited_claim_ids, list) or not cited_claim_ids:
            errors.append(
                f"accepted clinical guidance {record.get('claim_id', '<unknown>')} missing cited source claims"
            )
        derived = provenance.get("derived_from_claim_ids")
        if not isinstance(derived, list) or not derived:
            errors.append(
                f"clinical guidance {record.get('claim_id', '<unknown>')} provenance missing derived_from_claim_ids"
            )
        if record.get("review_status") == "accepted" and not record.get("reviewed_by"):
            errors.append(f"accepted clinical guidance {record.get('claim_id', '<unknown>')} missing reviewed_by")
    return errors
