"""Healthcare Scenario — Triage cohort envelope builder.

Builds the physiological-signal -> triage-classification envelope used as
the canonical clinical-triage sample. Each patient's suspected AF finding
is captured as an Interpretation-group SituationalStatement with a
SNOMED-coded finding in ``mainpart.object``, a ``situation.durability``
temporal marker, and an ``explanation.confidence`` the triage-routing
policy reads.

Output:
  - output/healthcare_triage_envelope.json
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from jhcontext import (
    ArtifactType,
    EnvelopeBuilder,
    PROVGraph,
    RiskLevel,
    interpretation,
)
from jhcontext.pii import InMemoryPIIVault


from jhcontext.pii import PIIMatch, is_pii_token


class _FieldsOnlyPIIDetector:
    """PII detector that flags named suppressed fields only — no regex auto-patterns.

    The SDK's DefaultPIIDetector scans values for generic patterns (email,
    phone, IP, SSN). Its phone regex matches 8-digit SNOMED codes, which
    corrupts clinical-finding values. This variant only flags the explicit
    ``suppressed_fields`` list, leaving SNOMED and other clinical IDs intact.
    """

    def __init__(self, suppressed_fields: list[str] | None = None) -> None:
        self.suppressed_fields = suppressed_fields or []

    def detect(self, value: str) -> list[tuple[str, str]]:
        return []

    def scan_payload(self, payload: list[dict]) -> list[PIIMatch]:
        results: list[PIIMatch] = []
        for idx, item in enumerate(payload):
            if isinstance(item, dict):
                self._scan_dict(item, f"[{idx}]", results)
        return results

    def _scan_dict(self, d: dict, prefix: str, results: list[PIIMatch]) -> None:
        for key, value in d.items():
            path = f"{prefix}.{key}"
            if key in self.suppressed_fields and isinstance(value, str) and not is_pii_token(value):
                results.append(PIIMatch(path, value, "suppressed_field"))
                continue
            if isinstance(value, dict):
                self._scan_dict(value, path, results)
            elif isinstance(value, list):
                for i, it in enumerate(value):
                    if isinstance(it, dict):
                        self._scan_dict(it, f"{path}[{i}]", results)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


# Synthetic triage cohort — mixture of confidence levels to exercise
# Art. 14 human-oversight routing.
TRIAGE_COHORT = [
    # (patient_id, snomed_code, finding_label, status, confidence, classifier)
    ("Patient/42",   "snomed:49436004", "atrial fibrillation",        "suspected", 0.87,  "ecg-classifier-v2.4"),
    ("Patient/7",    "snomed:49436004", "atrial fibrillation",        "suspected", 0.72,  "ecg-classifier-v2.4"),   # low conf → review
    ("Patient/103",  "snomed:49436004", "atrial fibrillation",        "suspected", 0.64,  "ecg-classifier-v2.4"),   # low conf
    ("Patient/58",   "snomed:59621000", "hypertensive disorder",      "confirmed", 0.94,  "ecg-classifier-v2.4"),
    ("Patient/201",  "snomed:49436004", "atrial fibrillation",        "suspected", 0.91,  "ecg-classifier-v2.4"),
    ("Patient/145",  "snomed:195080001", "chronic atrial fibrillation","confirmed",0.95,  "ecg-classifier-v2.4"),
]


def _build_envelope(patient_id: str, snomed: str, label: str,
                    status: str, confidence: float, classifier: str,
                    created_at: str) -> dict:
    stmt = interpretation(
        patient_id,
        "clinical_finding",
        {"code": snomed, "label": label, "status": status},
        range_="SNOMEDFindingCode",
        confidence=confidence,
        creator=f"did:example:{classifier}",
    )
    # Override default auxiliary with the clinical-finding idiom.
    stmt["mainpart"]["auxiliary"] = "hasFinding"
    stmt["situation"] = {"durability": "current"}

    builder = (
        EnvelopeBuilder()
        .set_producer(f"did:example:{classifier}")
        .set_scope("healthcare_triage")
        .set_ttl("PT15M")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([stmt])
        .add_artifact(
            artifact_id=f"ecg-observation-{patient_id.replace('/', '-')}",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:" + "e" * 64,
        )
        .add_artifact(
            artifact_id=classifier,
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:" + "c" * 64,
            model=classifier,
            confidence=confidence,
        )
        .set_passed_artifact(f"ecg-observation-{patient_id.replace('/', '-')}")
    )
    builder.set_privacy(
        data_category="sensitive",
        legal_basis="art_9_2_h_gdpr",
        retention="P5Y",
        storage_policy="hospital-encrypted",
        feature_suppression=["patient_name", "insurance_status",
                             "demographic_group"],
    )
    builder.set_compliance(risk_level=RiskLevel.HIGH, human_oversight_required=True)

    prov = PROVGraph(f"ctx-triage-{patient_id.replace('/', '-')}")
    prov.add_agent(classifier, f"ECG Classifier ({classifier})",
                   role="clinical_inference")
    prov.add_entity(f"ecg-observation-{patient_id.replace('/', '-')}",
                    "ECG observation",
                    artifact_type="token_sequence")
    prov.add_entity(f"finding-{patient_id.replace('/', '-')}",
                    "Suspected/confirmed finding",
                    artifact_type="semantic_extraction")
    prov.add_activity(
        f"triage-classification-{patient_id.replace('/', '-')}",
        "Triage classification",
        started_at=created_at, ended_at=created_at,
        method=classifier,
    )
    prov.used(f"triage-classification-{patient_id.replace('/', '-')}",
              f"ecg-observation-{patient_id.replace('/', '-')}")
    prov.was_generated_by(f"finding-{patient_id.replace('/', '-')}",
                          f"triage-classification-{patient_id.replace('/', '-')}")
    prov.was_associated_with(
        f"triage-classification-{patient_id.replace('/', '-')}", classifier)

    digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = digest

    envelope = builder.sign(f"did:example:{classifier}").build()
    return envelope.to_jsonld()


def run() -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    created_at = datetime(2026, 4, 18, 14, 2, 17, tzinfo=timezone.utc).isoformat()

    # A single combined "cohort envelope" with one Interpretation-group
    # statement per patient, so the SPARQL audit queries can surface the
    # cross-patient population-level signal in one query.
    cohort_payload: list[dict] = []
    for row in TRIAGE_COHORT:
        patient_id, snomed, label, status, confidence, classifier = row
        stmt = interpretation(
            patient_id,
            "clinical_finding",
            snomed,                                     # object = SNOMED code
            range_="SNOMEDFindingCode",
            confidence=confidence,
            creator=f"did:example:{classifier}",
        )
        stmt["mainpart"]["auxiliary"] = "hasFinding"
        stmt["explanation"]["finding_label"] = label    # label lives in explanation
        stmt["explanation"]["finding_status"] = status  # and status too
        stmt["situation"] = {"durability": "current"}
        cohort_payload.append(stmt)

    builder = (
        EnvelopeBuilder()
        .set_producer("did:example:ecg-classifier-v2.4")
        .set_scope("healthcare_triage_cohort")
        .set_ttl("PT1H")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload(cohort_payload)
        .add_artifact(
            artifact_id="ecg-classifier-v2.4",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:" + "c" * 64,
            model="ecg-classifier-v2.4",
        )
    )
    builder.set_privacy(
        data_category="sensitive",
        legal_basis="art_9_2_h_gdpr",
        retention="P5Y",
        storage_policy="hospital-encrypted",
        feature_suppression=["patient_name", "insurance_status", "demographic_group"],
    )
    builder.set_compliance(risk_level=RiskLevel.HIGH, human_oversight_required=True)
    builder.enable_pii_detachment(
        detector=_FieldsOnlyPIIDetector(
            suppressed_fields=["patient_name", "insurance_status", "demographic_group"]
        ),
        vault=InMemoryPIIVault(),
    )

    envelope = builder.sign("did:example:ecg-classifier-v2.4").build()

    env_path = OUTPUT_DIR / "healthcare_triage_envelope.json"
    env_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    metrics = {
        "patients": len(TRIAGE_COHORT),
        "envelope_build_ms": (time.perf_counter() - t0) * 1000,
        "envelope_size_bytes": env_path.stat().st_size,
        "output_path": str(env_path),
    }

    print("=" * 60)
    print("HEALTHCARE TRIAGE COHORT — clinical-triage envelope")
    print("=" * 60)
    print(f"  Patients:            {metrics['patients']}")
    print(f"  Envelope size:       {metrics['envelope_size_bytes']:,} bytes")
    print(f"  Envelope build time: {metrics['envelope_build_ms']:.1f} ms")
    print(f"  Output:              {metrics['output_path']}")
    print("=" * 60)

    return metrics


if __name__ == "__main__":
    run()
