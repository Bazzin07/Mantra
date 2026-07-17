"""Feature 4: Quality & Regulatory Compliance Intelligence.

Reuses the same graph substrate as RCA (`repository.documents_for_entity`) —
a regulation's documents are just its REGULATORY_REF entity's referencing
documents. Gap detection is a per-document keyword/procedure/incompleteness
scan, not a legal determination: every output is hedged accordingly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..config import Settings
from ..models import (
    Citation,
    ComplianceGap,
    ComplianceStatus,
    DocumentResponse,
    EquipmentCompliance,
    EvidencePackage,
    RegulationCompliance,
    RequirementResult,
)
from ..repository import Repository
from .embeddings import EmbeddingProvider
from .model_providers import DeterministicLLMProvider, LLMProvider

DEFAULT_FRAMEWORK_PATH = Path(__file__).resolve().parent.parent.parent / "regulatory_framework.json"

# Phrases that downgrade an otherwise-covered requirement to "partial" —
# ponytail: a fixed English phrase list, not NLP. Widen if real documents use
# different incompleteness language than this corpus's "incomplete"/"only two".
#
# Known ceiling (verified live, not theoretical): matching is per-document, not
# per-finding. A dense multi-finding audit report can pack an unrelated
# incompleteness phrase into the same 500-char chunk as a genuinely compliant
# finding (e.g. audit_finding_PESO-004.pdf's Finding 1 "license... No gap
# identified" sits in the same chunk as Finding 2's "...incomplete"), pulling a
# compliant requirement down to "partial". Accepted because it only ever
# pushes toward the more conservative label, never hides a real gap. Upgrade
# path: sentence-level or proximity-windowed matching if this proves too
# noisy on denser real documents.
INCOMPLETENESS_SIGNALS = ["incomplete", "only two", "overdue", "not documented", "missing", "gap identified"]

COMPLIANT_THRESHOLD = 0.75
PARTIAL_THRESHOLD = 0.35

DISCLAIMER = (
    "Requirement text is a synthetic paraphrase for demonstration, not the verbatim "
    "legal text of any regulation. Gap status is keyword/procedure-evidence based, "
    "not a legal compliance determination."
)


class ComplianceService:
    def __init__(
        self,
        repository: Repository,
        embedding_provider: EmbeddingProvider,
        settings: Optional[Settings] = None,
        llm_provider: Optional[LLMProvider] = None,
        framework_path: Optional[Path] = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.settings = settings or Settings()
        self.llm_provider = llm_provider or DeterministicLLMProvider()
        self.framework = _load_framework(framework_path or DEFAULT_FRAMEWORK_PATH)

    # ------------------------------------------------------------------
    # FR-23: compliance gap identification
    # ------------------------------------------------------------------
    async def gaps(self) -> List[ComplianceGap]:
        results: List[ComplianceGap] = []
        for regulation, spec in self.framework.items():
            documents = await self.repository.documents_for_entity("REGULATORY_REF", regulation)
            for requirement in spec["requirements"]:
                result = _evaluate_requirement(requirement, documents)
                if result.status != "compliant":
                    results.append(
                        ComplianceGap(
                            regulation=regulation,
                            requirement_id=result.requirement_id,
                            requirement_text=result.requirement_text,
                            status=result.status,
                            evidence=result.citations,
                            action_needed=result.action_needed,
                        )
                    )
        return results

    # ------------------------------------------------------------------
    # FR-25: compliance status dashboard
    # ------------------------------------------------------------------
    async def status(self) -> ComplianceStatus:
        regulations: List[RegulationCompliance] = []
        all_scores: List[float] = []
        for regulation, spec in self.framework.items():
            documents = await self.repository.documents_for_entity("REGULATORY_REF", regulation)
            requirement_results = [_evaluate_requirement(r, documents) for r in spec["requirements"]]
            all_scores.extend(r.coverage_score for r in requirement_results)
            counts = {"compliant": 0, "partial": 0, "gap": 0}
            for r in requirement_results:
                counts[r.status] += 1
            coverage_pct = round(sum(r.coverage_score for r in requirement_results) / max(len(requirement_results), 1) * 100, 1)
            regulations.append(
                RegulationCompliance(
                    regulation=regulation,
                    title=spec["title"],
                    requirements=requirement_results,
                    coverage_pct=coverage_pct,
                    status_counts=counts,
                )
            )
        overall = round(sum(all_scores) / max(len(all_scores), 1) * 100, 1)
        return ComplianceStatus(regulations=regulations, overall_coverage_pct=overall, framework_disclaimer=DISCLAIMER)

    # ------------------------------------------------------------------
    # FR-24: audit evidence package (structured JSON, not a PDF)
    # ------------------------------------------------------------------
    async def audit(self, regulation: str) -> EvidencePackage:
        spec = self.framework.get(_normalize_regulation_key(regulation))
        if not spec:
            return EvidencePackage(
                regulation=regulation, title="", requirements=[], coverage_pct=0.0,
                summary=f"No regulatory framework entry found for {regulation}.",
                generation_status="skipped_no_evidence", disclaimer=DISCLAIMER,
            )

        documents = await self.repository.documents_for_entity("REGULATORY_REF", regulation)
        requirement_results = [_evaluate_requirement(r, documents) for r in spec["requirements"]]
        coverage_pct = round(sum(r.coverage_score for r in requirement_results) / max(len(requirement_results), 1) * 100, 1)
        citations = [citation for r in requirement_results for citation in r.citations]

        if not citations:
            return EvidencePackage(
                regulation=regulation, title=spec["title"], requirements=requirement_results,
                coverage_pct=coverage_pct, summary=f"No indexed evidence found for {regulation}.",
                generation_status="skipped_no_evidence", disclaimer=DISCLAIMER,
            )

        model_name = self.settings.default_answer_model
        try:
            summary = await self.llm_provider.generate_answer(
                f"Summarize the compliance posture for {regulation} ({spec['title']}) based on the evidence.",
                citations, model_name, "compliance_audit",
            )
            generation_status = "llm"
        except Exception:
            top = max(requirement_results, key=lambda r: len(r.citations), default=None)
            summary = (
                f"{regulation}: {coverage_pct:.0f}% coverage across {len(requirement_results)} requirements. "
                f"See cited documents for detail." if top else f"No coverage detail available for {regulation}."
            )
            generation_status = "fallback"

        return EvidencePackage(
            regulation=regulation, title=spec["title"], requirements=requirement_results,
            coverage_pct=coverage_pct, summary=summary, generation_status=generation_status, disclaimer=DISCLAIMER,
        )

    # ------------------------------------------------------------------
    # /api/compliance/equipment/{id}
    # ------------------------------------------------------------------
    async def equipment_status(self, equipment_tag: str) -> EquipmentCompliance:
        graph = await self.repository.build_graph_for_entity("EQUIPMENT_TAG", equipment_tag)
        applicable = sorted(
            {node.metadata.get("normalized_text", node.label) for node in graph.nodes if node.type == "REGULATORY_REF"}
        )
        results: List[RequirementResult] = []
        for regulation in applicable:
            spec = self.framework.get(regulation)
            if not spec:
                continue
            documents = await self.repository.documents_for_entity("REGULATORY_REF", regulation)
            results.extend(_evaluate_requirement(r, documents) for r in spec["requirements"])
        return EquipmentCompliance(equipment_tag=equipment_tag, applicable_regulations=applicable, results=results)


def _normalize_regulation_key(regulation: str) -> str:
    return regulation.upper().replace(" ", "")


def _load_framework(path: Path) -> Dict[str, dict]:
    """Best-effort load; a missing/invalid file yields an empty framework
    (the service still runs, every method just returns empty/no-evidence)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {
        _normalize_regulation_key(key): value
        for key, value in (data.get("frameworks") or {}).items()
    }


def _evaluate_requirement(requirement: dict, documents: List[DocumentResponse]) -> RequirementResult:
    keywords = [kw.lower() for kw in requirement["evidence_keywords"]]
    procedure_prefixes = requirement["evidence_procedures"]

    best_score = 0.0
    best_incomplete = False
    citations: List[Citation] = []

    for document in documents:
        blob = " ".join(chunk.content.lower() for chunk in document.chunks)
        keyword_hit = any(kw in blob for kw in keywords)
        if not keyword_hit:
            continue

        procedure_hit = not procedure_prefixes or any(
            entity.entity_type == "PROCEDURE_ID" and any(entity.normalized_text.startswith(p) for p in procedure_prefixes)
            for entity in document.entities
        )
        incomplete_hit = any(signal in blob for signal in INCOMPLETENESS_SIGNALS)

        score = (0.6 if keyword_hit else 0.0) + (0.4 if procedure_hit else 0.0)
        if incomplete_hit:
            score = min(score, 0.5)
        best_score = max(best_score, score)
        best_incomplete = best_incomplete or incomplete_hit

        chunk = document.chunks[0] if document.chunks else None
        if chunk:
            citations.append(Citation(
                document_id=document.id, filename=document.metadata.filename, chunk_id=chunk.id,
                page_number=chunk.page_number, relevance_score=round(score, 4), excerpt=chunk.content[:320],
            ))

    status = "compliant" if best_score >= COMPLIANT_THRESHOLD else "partial" if best_score >= PARTIAL_THRESHOLD else "gap"
    if status == "gap":
        action_needed = f"No covering evidence found — provide documentation for: {requirement['text']}"
    elif status == "partial":
        reason = "incomplete records noted in the source document" if best_incomplete else "partial evidence only"
        action_needed = f"Close the gap ({reason}) for: {requirement['text']}"
    else:
        action_needed = ""

    return RequirementResult(
        requirement_id=requirement["id"], requirement_text=requirement["text"], status=status,
        coverage_score=round(best_score, 4), citations=citations, action_needed=action_needed,
    )
