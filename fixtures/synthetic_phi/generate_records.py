from __future__ import annotations

import argparse
import json
import random
import shutil
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = ROOT / "client_record_template.json"
DEFAULT_OUTPUT_DIR = ROOT / "records"
DEFAULT_COUNT = 300
BASE_TIMESTAMP = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
GENERATOR_NAME = "synthetic-phi-fixture-generator"
GENERATOR_VERSION = "0.1.0"
TEMPLATE_ID = "synthetic-phi-client-record-template-v1"
SCHEMA_VERSION = "0.1.0"
DATASET_ID = "synthetic-phi-development-dataset-001"

AGE_BANDS = {
    "child": (7, 12),
    "adolescent": (13, 17),
    "young_adult": (18, 25),
    "adult": (26, 64),
    "older_adult": (65, 82),
}

NAME_FIRST = [
    "Alex",
    "Avery",
    "Bailey",
    "Cameron",
    "Casey",
    "Devon",
    "Elliot",
    "Emerson",
    "Finley",
    "Harper",
    "Indigo",
    "Jordan",
    "Kai",
    "Logan",
    "Marlowe",
    "Morgan",
    "Parker",
    "Quinn",
    "Reese",
    "Rowan",
    "Sage",
    "Shiloh",
    "Skyler",
    "Taylor",
]

NAME_LAST = [
    "Arden",
    "Bennett",
    "Calloway",
    "Dawson",
    "Ellison",
    "Foster",
    "Granger",
    "Hollis",
    "Iverson",
    "Jamison",
    "Keaton",
    "Lennox",
    "Monroe",
    "Nolan",
    "Oakley",
    "Prescott",
    "Ramsey",
    "Sawyer",
    "Tatum",
    "Winslow",
]

PRONOUNS = [
    ("she/her", "woman"),
    ("he/him", "man"),
    ("they/them", "nonbinary"),
]

RACE_ETHNICITY_OPTIONS = [
    ["Synthetic Black or African diaspora heritage"],
    ["Synthetic Latino/a/e heritage"],
    ["Synthetic multiracial background"],
    ["Synthetic Asian heritage"],
    ["Synthetic white heritage"],
    ["Synthetic Middle Eastern or North African heritage"],
    ["Synthetic Native or Indigenous heritage"],
]

LANGUAGES = [
    "English",
    "Spanish",
    "English and Spanish",
    "English and American Sign Language",
    "English and Haitian Creole",
]

COMMUNICATION_NEEDS = [
    "none reported",
    "prefers written agenda at session start",
    "benefits from slower pacing for new material",
    "benefits from visual coping-plan reminders",
    "requests plain-language explanations",
]

ORGANIZATIONS = [
    "North Harbor Counseling Collective",
    "Juniper Path Behavioral Health",
    "Cedar Glen Family Services",
    "Bluebird Community Wellness",
    "Maple Horizon Psychiatry",
    "Riverstone Youth Supports",
    "Harborlight Recovery Partners",
]

REFERRAL_SOURCES = [
    "self referral after noticing escalating stress",
    "primary care referral for mood and sleep concerns",
    "school counselor referral for attendance and emotional regulation concerns",
    "employer assistance program referral",
    "caregiver referral after repeated conflict at home",
    "community agency referral during a life transition",
]

MEDICAL_HISTORY = [
    "seasonal allergies",
    "mild intermittent asthma",
    "migraine history",
    "tension headaches during stress",
    "postpartum recovery history",
    "hypertension monitored by primary care",
    "type 2 diabetes managed in primary care",
    "chronic pain related to prior orthopedic injury",
]

FAMILY_HISTORY = [
    "family history of anxiety symptoms",
    "family history of depressive episodes",
    "family history of alcohol misuse",
    "family history of attention and learning difficulties",
    "family history of trauma-related stress responses",
    "family history of dementia in an older relative",
]

SCENARIOS = [
    {
        "scenario_id": "anxiety_related",
        "title": "Anxiety-related distress",
        "presenting_summary": "persistent worry, physical tension, and avoidance affecting daily functioning",
        "onset": "symptoms have intensified over the last 6 months",
        "frequency": "most days with spikes before demands or transitions",
        "severity": ["mild", "moderate", "severe"],
        "impairments": ["school", "work", "sleep", "social"],
        "priority": "high",
        "diagnosis": ("SYN-F41.1", "Generalized anxiety disorder"),
        "history_label": "prior anxiety symptoms without consistent treatment",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low", "moderate"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "weekly",
        "focus": "reduce worry intensity and increase coping consistency",
    },
    {
        "scenario_id": "mood_related",
        "title": "Mood-related symptoms",
        "presenting_summary": "low mood, reduced motivation, and withdrawal from valued activities",
        "onset": "symptoms have been present for approximately 4 months",
        "frequency": "daily with worse functioning on unstructured days",
        "severity": ["moderate", "severe"],
        "impairments": ["work", "family", "self_care", "sleep"],
        "priority": "high",
        "diagnosis": ("SYN-F32.1", "Major depressive disorder, single episode, moderate"),
        "history_label": "intermittent depressive symptoms during previous stress periods",
        "medication": ("sertraline synthetic", "50 mg", "daily", "consistent"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low", "moderate"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "weekly",
        "focus": "increase activation, mood monitoring, and support use",
    },
    {
        "scenario_id": "trauma_related",
        "title": "Trauma-related stress",
        "presenting_summary": "intrusive memories, hypervigilance, and avoidance after a stressful event history",
        "onset": "symptoms have recurred over the past year after a triggering reminder",
        "frequency": "multiple times per week with sleep disruption",
        "severity": ["moderate", "severe"],
        "impairments": ["sleep", "family", "social", "work"],
        "priority": "high",
        "diagnosis": ("SYN-F43.10", "Posttraumatic stress disorder"),
        "history_label": "previous counseling related to traumatic stress",
        "medication": ("prazosin synthetic", "1 mg", "nightly", "inconsistent"),
        "substance": ("alcohol", "monthly", "within the last month", "low"),
        "trauma_reported": True,
        "risk_levels": ["low", "moderate"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "weekly",
        "focus": "stabilization, sleep support, and trauma-informed coping",
    },
    {
        "scenario_id": "attention_or_executive_functioning",
        "title": "Attention and executive functioning needs",
        "presenting_summary": "disorganization, incomplete tasks, and overwhelm with planning demands",
        "onset": "patterns have been noticeable since childhood and more disruptive in the last year",
        "frequency": "daily across home, school, or work routines",
        "severity": ["mild", "moderate"],
        "impairments": ["school", "work", "family"],
        "priority": "medium",
        "diagnosis": ("SYN-F90.0", "Attention-deficit/hyperactivity disorder, predominantly inattentive presentation"),
        "history_label": "attention concerns previously noted without formal follow-through",
        "medication": ("methylphenidate synthetic", "18 mg", "weekday mornings", "consistent"),
        "substance": ("caffeine", "daily", "today", "low"),
        "trauma_reported": False,
        "risk_levels": ["low"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "biweekly",
        "focus": "improve planning systems and reduce shame around task initiation",
    },
    {
        "scenario_id": "autism_or_neurodevelopmental_support_needs",
        "title": "Autism or neurodevelopmental support needs",
        "presenting_summary": "sensory overload, social fatigue, and need for structured routines and advocacy",
        "onset": "lifelong patterns have become more stressful in the current environment",
        "frequency": "daily with higher strain in noisy or socially demanding settings",
        "severity": ["mild", "moderate"],
        "impairments": ["school", "work", "social"],
        "priority": "medium",
        "diagnosis": ("SYN-F84.0", "Autism spectrum disorder support needs, synthetic profile"),
        "history_label": "past neurodevelopmental support discussions without coordinated planning",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low"],
        "recommendation": "community_referral",
        "service_frequency": "biweekly",
        "focus": "increase accommodations, self-advocacy, and sensory regulation planning",
    },
    {
        "scenario_id": "adjustment_or_life_transition",
        "title": "Adjustment and life transition stress",
        "presenting_summary": "stress, uncertainty, and reduced coping capacity during a major transition",
        "onset": "symptoms emerged within the last 3 months after a significant change",
        "frequency": "several times each week during planning or conflict situations",
        "severity": ["mild", "moderate"],
        "impairments": ["work", "family", "sleep"],
        "priority": "medium",
        "diagnosis": ("SYN-F43.23", "Adjustment disorder with mixed anxiety and depressed mood"),
        "history_label": "limited previous mental health treatment reported",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "biweekly",
        "focus": "stabilize routines and strengthen transition coping strategies",
    },
    {
        "scenario_id": "grief_or_loss",
        "title": "Grief and loss response",
        "presenting_summary": "waves of sadness, numbness, and role disruption after a meaningful loss",
        "onset": "symptoms began after a loss within the last 8 months",
        "frequency": "several times per week with anniversary triggers",
        "severity": ["moderate"],
        "impairments": ["family", "social", "sleep"],
        "priority": "high",
        "diagnosis": ("SYN-F43.21", "Adjustment disorder with depressed mood in grief context"),
        "history_label": "no previous formal grief-focused treatment reported",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low", "moderate"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "weekly",
        "focus": "support mourning, meaning-making, and reconnection with supports",
    },
    {
        "scenario_id": "substance_use_related",
        "title": "Substance-use-related concerns",
        "presenting_summary": "increasing reliance on substances to manage stress, sleep, or mood",
        "onset": "use has escalated over the last 9 months",
        "frequency": "multiple times per week with impaired follow-through the next day",
        "severity": ["moderate", "severe"],
        "impairments": ["work", "family", "sleep", "self_care"],
        "priority": "high",
        "diagnosis": ("SYN-F10.20", "Alcohol use disorder, moderate, synthetic"),
        "history_label": "prior attempts to cut down without structured support",
        "medication": ("naltrexone synthetic", "50 mg", "daily", "inconsistent"),
        "substance": ("alcohol", "4 to 5 days per week", "within the last week", "moderate"),
        "trauma_reported": False,
        "risk_levels": ["moderate"],
        "recommendation": "higher_level_evaluation",
        "service_frequency": "weekly",
        "focus": "reduce use, increase motivation, and expand sober supports",
    },
    {
        "scenario_id": "psychosis_spectrum_related",
        "title": "Psychosis-spectrum-related concerns",
        "presenting_summary": "unusual perceptual experiences, guardedness, and difficulty maintaining routines",
        "onset": "symptoms have intensified over the last 5 months",
        "frequency": "several times each week with intermittent sleep reversal",
        "severity": ["moderate", "severe"],
        "impairments": ["work", "family", "self_care", "sleep"],
        "priority": "high",
        "diagnosis": ("SYN-F29", "Unspecified schizophrenia spectrum and other psychotic disorder, synthetic"),
        "history_label": "prior urgent evaluation with partial stabilization",
        "medication": ("aripiprazole synthetic", "5 mg", "daily", "unknown"),
        "substance": ("cannabis", "weekly", "within the last 2 weeks", "moderate"),
        "trauma_reported": True,
        "risk_levels": ["moderate", "high"],
        "recommendation": "medication_evaluation",
        "service_frequency": "weekly",
        "focus": "stabilize sleep, reality testing, and medication follow-through",
    },
    {
        "scenario_id": "eating_or_body_image_related",
        "title": "Eating or body image concerns",
        "presenting_summary": "food restriction, body image distress, and rigid self-monitoring patterns",
        "onset": "concerns have increased over the last 7 months",
        "frequency": "daily preoccupation with episodic restriction behaviors",
        "severity": ["moderate"],
        "impairments": ["school", "work", "social", "self_care"],
        "priority": "high",
        "diagnosis": ("SYN-F50.9", "Unspecified feeding or eating disorder, synthetic"),
        "history_label": "past body image concerns with no sustained specialty care",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low", "moderate"],
        "recommendation": "higher_level_evaluation",
        "service_frequency": "weekly",
        "focus": "reduce restriction patterns and increase body-neutral coping",
    },
    {
        "scenario_id": "relationship_or_family_stress",
        "title": "Relationship or family stress",
        "presenting_summary": "conflict cycles, communication breakdowns, and emotional overwhelm in key relationships",
        "onset": "patterns have worsened over the last 4 months",
        "frequency": "weekly arguments with lingering distress between conflicts",
        "severity": ["mild", "moderate"],
        "impairments": ["family", "social", "sleep"],
        "priority": "medium",
        "diagnosis": ("SYN-Z63.8", "Relationship distress with spouse or intimate partner, synthetic"),
        "history_label": "similar conflict patterns reported in prior relationships",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "biweekly",
        "focus": "improve communication, boundaries, and repair strategies",
    },
    {
        "scenario_id": "school_or_work_functioning",
        "title": "School or work functioning strain",
        "presenting_summary": "declining performance, burnout, and reduced confidence with responsibilities",
        "onset": "difficulties have become more pronounced over the last academic or fiscal quarter",
        "frequency": "most weekdays with avoidance of high-demand tasks",
        "severity": ["mild", "moderate"],
        "impairments": ["school", "work", "sleep"],
        "priority": "medium",
        "diagnosis": ("SYN-Z56.9", "Occupational or educational problem, synthetic"),
        "history_label": "previous short-term counseling during a high-pressure period",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("caffeine", "daily", "today", "low"),
        "trauma_reported": False,
        "risk_levels": ["low"],
        "recommendation": "outpatient_therapy",
        "service_frequency": "biweekly",
        "focus": "restore pacing, performance confidence, and support use",
    },
    {
        "scenario_id": "caregiver_stress",
        "title": "Caregiver stress and role overload",
        "presenting_summary": "exhaustion, guilt, and limited recovery time while caring for others",
        "onset": "strain has accumulated over the last year",
        "frequency": "daily with frequent interruptions to rest and personal routines",
        "severity": ["moderate"],
        "impairments": ["family", "sleep", "self_care", "work"],
        "priority": "high",
        "diagnosis": ("SYN-Z63.6", "Dependent relative needing care at home, synthetic stress context"),
        "history_label": "prior caregiver support group participation with inconsistent attendance",
        "medication": ("none reported", "not_applicable", "not_applicable", "not_applicable"),
        "substance": ("none reported", "not_applicable", "not_applicable", "none"),
        "trauma_reported": False,
        "risk_levels": ["low", "moderate"],
        "recommendation": "community_referral",
        "service_frequency": "biweekly",
        "focus": "increase respite planning and reduce caregiver burnout",
    },
]

SECONDARY_SCENARIOS = [
    "anxiety_related",
    "adjustment_or_life_transition",
    "relationship_or_family_stress",
    "school_or_work_functioning",
]

REQUIRED_RECORD_FIELDS = [
    "record_id",
    "synthetic",
    "sensitivity",
    "client_record_id",
    "age_band",
    "demographics",
    "administrative_context",
    "presenting_problems",
    "diagnostic_profile",
    "clinical_history",
    "risk_assessment",
    "initial_evaluation",
    "treatment_plan",
    "session_notes",
    "record_provenance",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic client records from the fixture template.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def iso_datetime(offset_days: int = 0) -> str:
    return (BASE_TIMESTAMP + timedelta(days=offset_days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_slug(text: str) -> str:
    return text.lower().replace(" ", "_").replace("/", "_")


def years_ago(today: date, years: int, day_offset: int) -> date:
    month = ((today.month - 1 + day_offset) % 12) + 1
    day = min(today.day, 28)
    return date(today.year - years, month, day)


def select_age(index: int, age_band: str, rng: random.Random) -> int:
    low, high = AGE_BANDS[age_band]
    span = high - low + 1
    return low + ((index + rng.randint(0, span - 1)) % span)


def choose_name(index: int, rng: random.Random) -> tuple[str, str]:
    first = NAME_FIRST[(index + rng.randint(0, len(NAME_FIRST) - 1)) % len(NAME_FIRST)]
    last = NAME_LAST[(index * 2 + rng.randint(0, len(NAME_LAST) - 1)) % len(NAME_LAST)]
    return first, last


def choose_guardian(index: int, rng: random.Random) -> tuple[str, str]:
    first, last = choose_name(index + 1000, rng)
    relationship = rng.choice(["parent", "grandparent", "legal guardian", "foster caregiver"])
    return f"{first} {last}", relationship


def scenario_by_id(scenario_id: str) -> dict[str, Any]:
    return next(s for s in SCENARIOS if s["scenario_id"] == scenario_id)


def build_presenting_problem(problem_id: str, summary: str, scenario: dict[str, Any], severity: str) -> dict[str, Any]:
    return {
        "problem_id": problem_id,
        "summary": summary,
        "onset": scenario["onset"],
        "frequency": scenario["frequency"],
        "severity": severity,
        "functional_impairment": scenario["impairments"],
        "client_reported_priority": scenario["priority"],
    }


def build_diagnosis(
    diagnosis_id: str,
    scenario: dict[str, Any],
    status: str,
    source_fields: list[str],
    rationale: str,
) -> dict[str, Any]:
    code, label = scenario["diagnosis"]
    return {
        "diagnosis_id": diagnosis_id,
        "diagnostic_system": "DSM-5-style synthetic label",
        "code": code,
        "label": label,
        "status": status,
        "specifier_or_context": scenario["title"],
        "clinical_rationale_summary": rationale,
        "source_fields": source_fields,
    }


def build_release_of_information(age_band: str, rng: random.Random, intake_date: date) -> list[dict[str, str]]:
    expiration = (intake_date + timedelta(days=365)).isoformat()
    if age_band in {"child", "adolescent"}:
        return [
            {
                "collateral_party": rng.choice(["Synthetic school counselor", "Synthetic pediatrician"]),
                "authorization_scope": rng.choice(["limited", "full"]),
                "expiration_date": expiration,
            }
        ]
    return [
        {
            "collateral_party": rng.choice(
                [
                    "Synthetic primary care clinician",
                    "Synthetic partner",
                    "Synthetic employer support contact",
                ]
            ),
            "authorization_scope": rng.choice(["none", "limited"]),
            "expiration_date": expiration,
        }
    ]


def build_care_team(age_band: str, rng: random.Random) -> list[dict[str, str]]:
    team = [
        {
            "role": "clinician",
            "synthetic_name": f"{choose_name(3000, rng)[0]} {choose_name(3001, rng)[1]}, LCSW",
            "organization": rng.choice(ORGANIZATIONS),
        }
    ]
    if age_band in {"child", "adolescent"}:
        team.append(
            {
                "role": "collateral_contact",
                "synthetic_name": "Synthetic school support liaison",
                "organization": "North Ridge School Supports",
            }
        )
    else:
        team.append(
            {
                "role": "prescriber",
                "synthetic_name": f"{choose_name(3010, rng)[0]} {choose_name(3011, rng)[1]}, PMHNP",
                "organization": rng.choice(ORGANIZATIONS),
            }
        )
    return team


def build_social_history(age_band: str, scenario: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    settings = {
        "child": "school",
        "adolescent": "school",
        "young_adult": rng.choice(["college", "work"]),
        "adult": "work",
        "older_adult": rng.choice(["retired", "other_synthetic"]),
    }
    setting = settings[age_band]
    supports = {
        "school": ["scheduled check-ins with school support staff", "extended time on selected assignments"],
        "college": ["reduced course load planning", "campus counseling coordination"],
        "work": ["structured task list", "protected break reminders"],
        "retired": ["senior center routine", "transportation planning support"],
        "other_synthetic": ["community support group", "family respite coordination"],
    }
    living = {
        "child": "lives with caregiver in a stable synthetic household",
        "adolescent": "splits time between caregivers with predictable routines",
        "young_adult": "lives with roommates or family while building independence",
        "adult": "lives with partner, family, or alone in stable housing",
        "older_adult": "lives independently with periodic family support",
    }
    relationships = {
        "child": "close attachment to caregiver with periodic conflict around routines",
        "adolescent": "supportive peer relationships with some withdrawal during stress",
        "young_adult": "mixed confidence in close relationships during transitions",
        "adult": "maintains a few trusted supports but tends to isolate under strain",
        "older_adult": "values family connection and routine community contact",
    }
    return {
        "living_situation": living[age_band],
        "relationships": relationships[age_band],
        "cultural_or_spiritual_factors": rng.choice(
            [
                "values family loyalty and practical support",
                "draws meaning from a synthetic faith community",
                "prefers culturally responsive, collaborative care",
                "identifies creativity and community service as grounding values",
            ]
        ),
        "school_or_work": {
            "setting": setting,
            "functioning_summary": (
                f"Current {setting} functioning is affected by {scenario['presenting_summary']} "
                "with stronger performance on structured days."
            ),
            "supports_or_accommodations": supports[setting],
        },
        "legal_or_system_involvement": "none_reported",
    }


def build_risk_assessment(
    age_band: str,
    scenario: dict[str, Any],
    severity: str,
    intake_date: date,
    rng: random.Random,
) -> dict[str, Any]:
    risk_level = rng.choice(scenario["risk_levels"])
    if scenario["scenario_id"] == "psychosis_spectrum_related" and severity == "severe":
        risk_level = "high"
    suicidal = "denied"
    self_harm = "denied"
    homicidal = "denied"
    safety_plan = "not_indicated"
    resources_reviewed = False
    if risk_level == "moderate":
        suicidal = rng.choice(["denied", "passive"])
        safety_plan = "reviewed"
        resources_reviewed = True
    if risk_level == "high":
        suicidal = "active_without_plan"
        self_harm = "historical"
        safety_plan = "referred_to_higher_care"
        resources_reviewed = True
    abuse = "none_reported"
    if age_band in {"child", "adolescent"} and scenario["scenario_id"] in {
        "trauma_related",
        "relationship_or_family_stress",
    }:
        abuse = "historical"
    return {
        "assessment_date": intake_date.isoformat(),
        "suicidal_ideation": suicidal,
        "self_harm": self_harm,
        "homicidal_ideation": homicidal,
        "abuse_or_neglect_concerns": abuse,
        "risk_level": risk_level,
        "protective_factors": [
            rng.choice(
                [
                    "engaged caregiver or family support",
                    "future-oriented goals",
                    "consistent attendance and help-seeking",
                    "connection to school, work, or community routine",
                    "identified coping strategies that provide relief",
                ]
            )
        ],
        "safety_plan_status": safety_plan,
        "crisis_resources_reviewed": resources_reviewed,
    }


def build_goal(
    goal_id: str,
    objective_id: str,
    scenario: dict[str, Any],
    intake_date: date,
    rng: random.Random,
) -> dict[str, Any]:
    problem_area = scenario["title"]
    return {
        "goal_id": goal_id,
        "problem_area": problem_area,
        "goal_statement": f"Client will {scenario['focus']} over the next 90 days.",
        "baseline": (
            f"Current baseline includes {scenario['presenting_summary']} with impairment "
            f"across {', '.join(scenario['impairments'][:2])}."
        ),
        "target": (
            "Client will report clearer coping gains and improved functioning "
            f"related to {problem_area.lower()}."
        ),
        "measurement_methods": [
            "client self-report",
            "caregiver report when appropriate",
            "clinician observation",
            "rating scale or symptom tracker",
            "attendance or functioning data",
        ],
        "target_date": (intake_date + timedelta(days=90)).isoformat(),
        "progress_status": "in_progress",
        "objectives": [
            {
                "objective_id": objective_id,
                "objective_statement": (
                    "Client will practice two agreed coping or communication strategies "
                    "between sessions and review barriers in session."
                ),
                "target_date": (intake_date + timedelta(days=45)).isoformat(),
                "status": rng.choice(["not_started", "in_progress"]),
            }
        ],
        "planned_interventions": [
            "psychoeducation",
            "CBT skill building",
            "DBT-informed emotion regulation skills",
            "family or caregiver collateral when clinically appropriate",
            "motivational interviewing",
            "safety planning when clinically indicated",
            "care coordination with authorized collateral contacts",
        ],
    }


def build_session_note(
    note_id: str,
    session_date: date,
    session_type: str,
    diagnosis_ids: list[str],
    goal_ids: list[str],
    risk_level: str,
    scenario: dict[str, Any],
    age_band: str,
    rng: random.Random,
) -> dict[str, Any]:
    mood_map = {
        "low": "anxious but engaged",
        "moderate": "stressed and somewhat constricted",
        "high": "guarded and emotionally overloaded",
    }
    observation = mood_map[risk_level]
    return {
        "note_id": note_id,
        "session_date": session_date.isoformat(),
        "session_type": session_type,
        "duration_minutes": 45 if session_type == "individual" else 50,
        "diagnoses_addressed": diagnosis_ids,
        "goals_addressed": goal_ids,
        "mental_status": {
            "appearance": "casually dressed and appropriately groomed",
            "behavior": "cooperative with mild restlessness during difficult topics",
            "orientation": "oriented to person, place, time, and situation",
            "speech": "normal rate and volume",
            "mood": observation,
            "affect": "congruent with discussed stressors",
            "thought_process": "organized and goal directed",
            "thought_content": "focused on current stressors without delusional elaboration",
            "perception": "no acute perceptual disturbance observed in session",
            "cognition": "attention adequate for session tasks",
            "insight": rng.choice(["fair", "good"]),
            "judgment": rng.choice(["fair", "good"]),
            "risk_observation": f"Risk presentation remained {risk_level} with no acute escalation during session.",
        },
        "intervention_provided": [
            "Synthetic session focused on "
            f"{scenario['focus']} using collaborative skill rehearsal and reflective questioning."
        ],
        "client_response": (
            "Client was receptive to structured support, identified barriers honestly, "
            "and described at least one situation where the intervention could be applied."
        ),
        "plan": [
            "Continue current treatment focus and review between-session practice next visit.",
            "Monitor functioning changes and coordinate with authorized supports when needed.",
        ],
        "homework_or_between_session_practice": [
            rng.choice(
                [
                    "track symptom intensity once daily",
                    "practice a grounding or breathing exercise three times this week",
                    "use a written planning tool before one high-stress task",
                    "schedule one restorative activity before the next session",
                ]
            )
        ],
        "progress_toward_goals": [
            {
                "goal_id": goal_ids[0],
                "progress_summary": (
                    f"Client showed early progress toward {scenario['focus']} "
                    "with ongoing need for repetition and support."
                ),
                "progress_status": rng.choice(["in_progress", "partially_met"]),
            }
        ],
        "risk_level": risk_level,
        "next_session_focus": [
            f"Further work on {scenario['focus']}",
            "Review barriers, supports, and pacing between sessions",
        ],
        "provenance": {
            "source_type": "synthetic_generator",
            "template_id": TEMPLATE_ID,
            "scenario_id": scenario["scenario_id"],
            "generator_version": GENERATOR_VERSION,
            "claim_scope": "session_note",
            "clinical_review_status": "not_clinician_reviewed",
        },
    }


def build_record(index: int, template: dict[str, Any]) -> dict[str, Any]:
    rng = random.Random(8200 + index)
    age_band = list(AGE_BANDS)[(index - 1) % len(AGE_BANDS)]
    primary = SCENARIOS[(index - 1) % len(SCENARIOS)]
    secondary = scenario_by_id(SECONDARY_SCENARIOS[(index - 1) % len(SECONDARY_SCENARIOS)])
    severity = primary["severity"][(index + rng.randint(0, len(primary["severity"]) - 1)) % len(primary["severity"])]
    age = select_age(index, age_band, rng)
    intake_date = date(2026, 1, 5) + timedelta(days=(index * 3) % 120)
    dob = years_ago(intake_date, age, index % 11)
    first, last = choose_name(index, rng)
    full_name = f"{first} {last}"
    preferred_name = first if rng.random() > 0.3 else f"{first} {last[0]}."
    pronouns, gender_identity = PRONOUNS[index % len(PRONOUNS)]
    race_ethnicity = RACE_ETHNICITY_OPTIONS[(index - 1) % len(RACE_ETHNICITY_OPTIONS)]
    guardian_required = age_band in {"child", "adolescent"}
    guardian_name, guardian_relationship = choose_guardian(index, rng)
    med_name, med_dose, med_frequency, med_adherence = primary["medication"]
    substance_name, substance_frequency, substance_last_use, concern_level = primary["substance"]
    code, diagnosis_label = primary["diagnosis"]
    diagnosis_id = f"diagnosis-{index:03d}-001"
    secondary_diagnosis_id = f"diagnosis-{index:03d}-002"
    problem_id = f"problem-{index:03d}-001"
    secondary_problem_id = f"problem-{index:03d}-002"
    goal_id = f"goal-{index:03d}-001"
    objective_id = f"obj-{index:03d}-001"
    clinician_first, clinician_last = choose_name(index + 500, rng)
    clinician_name = f"{clinician_first} {clinician_last}"
    assessment = build_risk_assessment(age_band, primary, severity, intake_date, rng)
    treatment_goal = build_goal(goal_id, objective_id, primary, intake_date, rng)
    initial_problem = build_presenting_problem(
        problem_id,
        f"Client presents with {primary['presenting_summary']}.",
        primary,
        severity,
    )
    secondary_problem = build_presenting_problem(
        secondary_problem_id,
        f"Client also reports {secondary['presenting_summary']} in the current context.",
        secondary,
        secondary["severity"][0],
    )
    diagnoses = [
        build_diagnosis(
            diagnosis_id,
            primary,
            "active",
            [
                "initial_evaluation.history_of_present_illness",
                "initial_evaluation.mental_status_exam",
                "risk_assessment",
            ],
            f"Generated record reflects {primary['presenting_summary']} with impairment across key settings.",
        )
    ]
    if secondary["scenario_id"] != primary["scenario_id"]:
        diagnoses.append(
            build_diagnosis(
                secondary_diagnosis_id,
                secondary,
                "provisional",
                [
                    "presenting_problems",
                    "clinical_history.social_history",
                    "initial_evaluation.biopsychosocial_summary.social",
                ],
                "Secondary stress pattern is consistent with "
                f"{secondary['title'].lower()} and informs treatment planning.",
            )
        )
    record = deepcopy(template["client_record_template"])
    record["record_id"] = f"client-synthetic-{index:03d}"
    record["client_record_id"] = record["record_id"]
    record["pseudonymous_case_label"] = f"Synthetic Case {index:03d}"
    record["age_band"] = age_band
    record["demographics"] = {
        "synthetic_full_name": full_name,
        "preferred_name": preferred_name,
        "date_of_birth": dob.isoformat(),
        "age_at_intake": age,
        "pronouns": pronouns,
        "gender_identity": gender_identity,
        "race_ethnicity": race_ethnicity,
        "primary_language": LANGUAGES[(index - 1) % len(LANGUAGES)],
        "communication_needs": [COMMUNICATION_NEEDS[(index - 1) % len(COMMUNICATION_NEEDS)]],
        "household_composition": (
            ["caregiver", "sibling", "client"] if guardian_required else ["client", "partner or chosen support"]
        ),
        "guardian_or_caregiver": {
            "required_for_record": guardian_required,
            "synthetic_name": guardian_name if guardian_required else "not_applicable",
            "relationship": guardian_relationship if guardian_required else "not_applicable",
            "contact_authorization_status": "authorized" if guardian_required else "not_applicable",
        },
    }
    record["administrative_context"] = {
        "intake_date": intake_date.isoformat(),
        "referral_source": REFERRAL_SOURCES[(index - 1) % len(REFERRAL_SOURCES)],
        "payer_type": ["commercial", "medicaid", "medicare", "self_pay", "grant_funded", "other_synthetic"][
            (index - 1) % 6
        ],
        "service_line": "outpatient_therapy",
        "consent_status": "minor_assent_with_guardian_consent" if guardian_required else "obtained",
        "release_of_information": build_release_of_information(age_band, rng, intake_date),
        "care_team": build_care_team(age_band, rng),
    }
    record["presenting_problems"] = [initial_problem, secondary_problem]
    record["diagnostic_profile"] = {
        "current_diagnostic_impressions": diagnoses,
        "diagnosis_history": [
            {
                "label": primary["history_label"],
                "timeframe": "reported during intake history review",
                "reported_by": "guardian" if guardian_required else "client",
            }
        ],
    }
    record["clinical_history"] = {
        "medications": [
            {
                "name": med_name,
                "dose": med_dose,
                "frequency": med_frequency,
                "adherence": med_adherence,
                "prescriber": "Synthetic prescribing clinician" if med_name != "none reported" else "not_applicable",
            }
        ],
        "allergies": [
            {
                "substance": rng.choice(["none reported", "penicillin synthetic", "latex synthetic"]),
                "reaction": rng.choice(["not_applicable", "rash", "mild gastrointestinal upset"]),
            }
        ],
        "medical_history": [MEDICAL_HISTORY[(index - 1) % len(MEDICAL_HISTORY)]],
        "psychiatric_history": [
            {
                "service_type": rng.choice(
                    ["therapy", "psychiatry", "intensive_outpatient", "crisis", "none_reported"]
                ),
                "timeframe": "within the last 1 to 3 years",
                "summary": "Synthetic prior service summary focused on symptom management and support engagement.",
            }
        ],
        "substance_use": {
            "screening_summary": (
                f"Screening indicates {substance_name} use pattern of "
                f"{substance_frequency} with concern level {concern_level}."
            ),
            "substances": [
                {
                    "substance": substance_name,
                    "frequency": substance_frequency,
                    "last_use": substance_last_use,
                    "clinical_concern_level": concern_level,
                }
            ],
        },
        "trauma_history": {
            "reported": primary["trauma_reported"],
            "summary": (
                "Client reports a prior stressful event history that remains relevant to current coping."
                if primary["trauma_reported"]
                else "No trauma history reported beyond normative life stressors."
            ),
            "current_safety_concerns": "none_reported",
        },
        "family_history": [FAMILY_HISTORY[(index - 1) % len(FAMILY_HISTORY)]],
        "social_history": build_social_history(age_band, primary, rng),
    }
    record["risk_assessment"] = assessment
    record["initial_evaluation"] = {
        "evaluation_id": f"eval-client-synthetic-{index:03d}-001",
        "evaluation_date": intake_date.isoformat(),
        "clinician": {
            "synthetic_name": clinician_name,
            "role": rng.choice(["licensed_clinician", "supervised_clinician", "intake_clinician"]),
            "signature": f"{clinician_name}, Synthetic Credential",
        },
        "reason_for_referral": f"Referral requested support for {primary['title'].lower()}.",
        "chief_concern": (
            f"Client described {primary['presenting_summary']} as the main reason for seeking help."
            if not guardian_required
            else f"Caregiver reported concern about {primary['presenting_summary']} and related functioning changes."
        ),
        "history_of_present_illness": (
            f"{primary['onset']}; {primary['frequency']}; severity currently described as {severity}. "
            f"Symptoms include {primary['presenting_summary']} and interfere with {', '.join(primary['impairments'])}."
        ),
        "biopsychosocial_summary": {
            "biological": f"Biological considerations include {MEDICAL_HISTORY[(index + 1) % len(MEDICAL_HISTORY)]}.",
            "psychological": (
                "Psychological factors include "
                f"{primary['presenting_summary']} with variable self-criticism."
            ),
            "social": f"Social stressors include {secondary['presenting_summary']} and changing role demands.",
            "strengths": [
                "shows insight and willingness to participate",
                "has at least one reliable support person",
            ],
            "barriers": [
                "stress increases avoidance and follow-through problems",
                "limited recovery time between demands",
            ],
        },
        "mental_status_exam": {
            "appearance": "casually dressed and age appropriate",
            "behavior": "engaged with mild psychomotor tension",
            "orientation": "oriented x4",
            "speech": "clear and coherent",
            "mood": rng.choice(["anxious", "sad", "overwhelmed", "guarded but hopeful"]),
            "affect": "congruent and appropriately reactive",
            "thought_process": "linear and goal directed",
            "thought_content": f"preoccupied with {primary['title'].lower()}",
            "perception": "no acute abnormalities observed",
            "cognition": "grossly intact",
            "insight": rng.choice(["fair", "good"]),
            "judgment": rng.choice(["fair", "good"]),
        },
        "diagnostic_impressions": [diagnosis_label, secondary["diagnosis"][1]],
        "clinical_formulation": (
            f"Clinical formulation integrates {primary['presenting_summary']}, developmental context, strengths, "
            f"and current functional impairment across {', '.join(primary['impairments'][:3])}."
        ),
        "recommended_level_of_care": primary["recommendation"],
        "initial_treatment_recommendations": [
            "begin structured outpatient therapy focused on symptom tracking and coping",
            "review support system involvement and care coordination needs",
            "consider additional referral or medication evaluation when clinically indicated",
        ],
        "collateral_or_referral_needs": [
            "coordinate with authorized collateral contact when it supports care goals",
            "monitor whether specialty referral is needed as treatment progresses",
        ],
        "provenance": {
            "source_type": "synthetic_generator",
            "template_id": TEMPLATE_ID,
            "scenario_id": primary["scenario_id"],
            "generator_version": GENERATOR_VERSION,
            "claim_scope": "initial_evaluation",
            "clinical_review_status": "not_clinician_reviewed",
        },
    }
    record["treatment_plan"] = {
        "plan_id": f"txplan-client-synthetic-{index:03d}-001",
        "created_date": intake_date.isoformat(),
        "review_due_date": (intake_date + timedelta(days=90)).isoformat(),
        "level_of_care": "outpatient_therapy",
        "service_frequency": primary["service_frequency"],
        "estimated_duration": rng.choice(["12 weeks", "16 weeks", "6 months"]),
        "diagnoses_addressed": [d["diagnosis_id"] for d in diagnoses],
        "presenting_problems_addressed": [problem_id, secondary_problem_id],
        "client_strengths": [
            "motivation to improve daily functioning",
            "capacity to identify helpful supports",
        ],
        "barriers_to_progress": [
            "stress-related avoidance",
            "limited energy during high-demand periods",
        ],
        "smart_goals": [treatment_goal],
        "care_coordination_plan": [
            "Synthetic care coordination step with authorized collateral contact.",
            "Synthetic referral or follow-up step when clinically indicated.",
        ],
        "minimal_discharge_criteria": deepcopy(
            template["client_record_template"]["treatment_plan"]["minimal_discharge_criteria"]
        ),
        "provenance": {
            "source_type": "synthetic_generator",
            "template_id": TEMPLATE_ID,
            "scenario_id": primary["scenario_id"],
            "generator_version": GENERATOR_VERSION,
            "claim_scope": "treatment_plan",
            "clinical_review_status": "not_clinician_reviewed",
        },
    }
    record["session_notes"] = [
        build_session_note(
            note_id=f"note-client-synthetic-{index:03d}-001",
            session_date=intake_date + timedelta(days=7),
            session_type="individual" if not guardian_required else "intake_follow_up",
            diagnosis_ids=[d["diagnosis_id"] for d in diagnoses],
            goal_ids=[goal_id],
            risk_level=assessment["risk_level"],
            scenario=primary,
            age_band=age_band,
            rng=rng,
        ),
        build_session_note(
            note_id=f"note-client-synthetic-{index:03d}-002",
            session_date=intake_date + timedelta(days=21),
            session_type="caregiver_collateral" if guardian_required and rng.random() > 0.5 else "individual",
            diagnosis_ids=[d["diagnosis_id"] for d in diagnoses],
            goal_ids=[goal_id],
            risk_level=assessment["risk_level"],
            scenario=primary,
            age_band=age_band,
            rng=rng,
        ),
    ]
    record["record_provenance"] = {
        "source_type": "synthetic_generator",
        "template_id": TEMPLATE_ID,
        "scenario_id": primary["scenario_id"],
        "seed": f"synthetic-seed-{8200 + index}",
        "generator_version": GENERATOR_VERSION,
        "generated_at": iso_datetime(index),
        "clinical_review_status": "not_clinician_reviewed",
    }
    record["validation_expectations"] = {
        "required_top_level_fields": REQUIRED_RECORD_FIELDS,
        "reject_if": deepcopy(template["client_record_template"]["validation_expectations"]["reject_if"]),
    }
    return {
        "template_id": template["template_id"],
        "template_version": template["template_version"],
        "description": template["description"],
        "record_policy": deepcopy(template["record_policy"]),
        "dataset_metadata": {
            "dataset_id": DATASET_ID,
            "generated_at": iso_datetime(index),
            "generator_name": GENERATOR_NAME,
            "generator_version": GENERATOR_VERSION,
            "schema_version": SCHEMA_VERSION,
            "record_count": 1,
            "lifespan_coverage": list(template["dataset_metadata"]["lifespan_coverage"]),
            "clinical_variety_targets": list(template["dataset_metadata"]["clinical_variety_targets"]),
        },
        "client_record": record,
    }


def validate_record(payload: dict[str, Any]) -> None:
    assert payload["record_policy"]["synthetic"] is True
    assert payload["dataset_metadata"]["record_count"] == 1
    record = payload["client_record"]
    for field in REQUIRED_RECORD_FIELDS:
        assert field in record, field
    assert record["synthetic"] is True
    assert record["sensitivity"] == "synthetic"
    assert record["initial_evaluation"]["provenance"]["claim_scope"] == "initial_evaluation"
    assert record["treatment_plan"]["smart_goals"]
    assert record["session_notes"]
    assert all(note["provenance"]["claim_scope"] == "session_note" for note in record["session_notes"])


def main() -> None:
    args = parse_args()
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    output_dir = args.output_dir
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, args.count + 1):
        payload = build_record(index, template)
        validate_record(payload)
        path = output_dir / f"client-synthetic-{index:03d}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
