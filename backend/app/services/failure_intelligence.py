"""Feature 5: Lessons Learned & Failure Intelligence Engine.

The systemic-pattern engine (FR-27) is RCAService.cluster_failures() — the
same DBSCAN-over-real-embeddings clustering built and calibrated for FR-20 —
called and enriched here with affected-equipment and severity-trend data
rather than reimplemented. FR-28's similarity search and FR-29's proactive
check reuse the same "failure-bearing chunk" concept and cosine_similarity
helper the clustering already uses.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from ..config import Settings
from ..models import (
    Citation,
    DocumentChunk,
    DocumentResponse,
    FailurePattern,
    FailureWarning,
    IncidentAnalysis,
    PatternReport,
    SimilarIncident,
    SimilarIncidentReport,
)
from ..repository import Repository
from .embeddings import EmbeddingProvider, HashingEmbeddingProvider, cosine_similarity
from .model_providers import DeterministicLLMProvider, LLMProvider
from .rca import RCAService, _parse_date

WARNING_THRESHOLD = 0.75


class FailureIntelligenceService:
    def __init__(
        self,
        repository: Repository,
        embedding_provider: EmbeddingProvider,
        rca_service: RCAService,
        settings: Optional[Settings] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.rca_service = rca_service
        self.settings = settings or Settings()
        self.llm_provider = llm_provider or DeterministicLLMProvider()

    # ------------------------------------------------------------------
    # FR-26: incident report analysis
    # ------------------------------------------------------------------
    async def analyze_incident(self, document_id: str) -> Optional[IncidentAnalysis]:
        document = await self.repository.get_document(document_id)
        if not document:
            return None

        contributing_factors = sorted({e.text for e in document.entities if e.entity_type == "FAILURE_MODE"})
        affected_equipment = sorted({e.text for e in document.entities if e.entity_type == "EQUIPMENT_TAG"})

        if not contributing_factors:
            return IncidentAnalysis(
                document_id=document.id, filename=document.metadata.filename,
                contributing_factors=[], affected_equipment=affected_equipment,
                root_cause_summary="No failure indicators were found in this document.",
                generation_status="skipped_no_evidence",
            )

        citations = [
            Citation(
                document_id=document.id, filename=document.metadata.filename, chunk_id=chunk.id,
                page_number=chunk.page_number, relevance_score=1.0, excerpt=chunk.content[:320],
            )
            for chunk in document.chunks[:3]
        ]
        try:
            summary = await self.llm_provider.generate_answer(
                f"Summarize the contributing factors and likely root cause in {document.metadata.filename}.",
                citations, self.settings.default_answer_model, "incident_analysis",
            )
            generation_status = "llm"
        except Exception:
            summary = (
                f"Contributing factors identified: {', '.join(contributing_factors)}. "
                f"Affected equipment: {', '.join(affected_equipment) if affected_equipment else 'none identified'}. "
                "Review the source document for the full account before drawing conclusions."
            )
            generation_status = "fallback"

        return IncidentAnalysis(
            document_id=document.id, filename=document.metadata.filename,
            contributing_factors=contributing_factors, affected_equipment=affected_equipment,
            root_cause_summary=summary, generation_status=generation_status,
        )

    # ------------------------------------------------------------------
    # FR-27: systemic pattern detection (wraps RCA's calibrated clustering)
    # ------------------------------------------------------------------
    async def patterns(self) -> PatternReport:
        cluster_report = await self.rca_service.cluster_failures()
        if not cluster_report.available:
            return PatternReport(available=False, reason=cluster_report.reason)
        if not cluster_report.clusters:
            return PatternReport(available=True, patterns=[])

        equipment_by_filename, dates_by_filename = await self._document_metadata_index()

        patterns: List[FailurePattern] = []
        for cluster in cluster_report.clusters:
            equipment: Set[str] = set()
            dates: List[datetime] = []
            for filename in cluster.document_filenames:
                equipment.update(equipment_by_filename.get(filename, set()))
                dates.extend(dates_by_filename.get(filename, []))

            if len(dates) >= 2:
                span = max(dates) - min(dates)
                severity_trend = "escalating" if span <= timedelta(days=7) else "recurring"
            else:
                severity_trend = "unclassified"

            patterns.append(
                FailurePattern(
                    cluster_id=cluster.cluster_id,
                    description=f"Recurring {', '.join(cluster.failure_terms)}" if cluster.failure_terms else "Recurring failure pattern",
                    frequency=cluster.member_count,
                    affected_equipment=sorted(equipment),
                    severity_trend=severity_trend,
                    document_filenames=cluster.document_filenames,
                )
            )
        return PatternReport(available=True, patterns=patterns)

    async def _document_metadata_index(self) -> Tuple[Dict[str, Set[str]], Dict[str, List[datetime]]]:
        """filename -> {equipment tags} and filename -> [dates], built once from
        the same document set cluster_failures() already scans."""
        equipment: Dict[str, Set[str]] = defaultdict(set)
        dates: Dict[str, List[datetime]] = defaultdict(list)
        seen_documents: Set[str] = set()
        for document, _chunk in await self.repository.iter_chunks():
            if document.id in seen_documents:
                continue
            seen_documents.add(document.id)
            filename = document.metadata.filename
            for entity in document.entities:
                if entity.entity_type == "EQUIPMENT_TAG":
                    equipment[filename].add(entity.text)
                elif entity.entity_type == "DATE":
                    parsed = _parse_date(entity.text)
                    if parsed:
                        dates[filename].append(parsed)
        return equipment, dates

    # ------------------------------------------------------------------
    # FR-28: similar incident search
    # ------------------------------------------------------------------
    async def similar_incidents(self, document_id: str, top_k: int = 10) -> Optional[SimilarIncidentReport]:
        seed = await self.repository.get_document(document_id)
        if not seed:
            return None

        seed_vectors = [chunk.embedding for chunk in seed.chunks if chunk.embedding]
        scored: List[Tuple[float, DocumentResponse, DocumentChunk]] = []
        seen_documents: Set[str] = {seed.id}
        for document, chunk in await self.repository.iter_chunks():
            if document.id in seen_documents or not chunk.embedding:
                continue
            best = max((cosine_similarity(sv, chunk.embedding) for sv in seed_vectors), default=0.0)
            scored.append((best, document, chunk))

        # Keep only each candidate document's single best-matching chunk.
        best_per_document: Dict[str, Tuple[float, DocumentResponse, DocumentChunk]] = {}
        for score, document, chunk in scored:
            current = best_per_document.get(document.id)
            if current is None or score > current[0]:
                best_per_document[document.id] = (score, document, chunk)

        ranked = sorted(best_per_document.values(), key=lambda item: item[0], reverse=True)[:top_k]
        similar = [
            SimilarIncident(
                document_id=document.id, filename=document.metadata.filename,
                similarity_score=round(score, 4), lessons_learned=chunk.content[:320],
            )
            for score, document, chunk in ranked
        ]
        return SimilarIncidentReport(seed_document_id=seed.id, seed_filename=seed.metadata.filename, similar_incidents=similar)

    # ------------------------------------------------------------------
    # FR-29: proactive warning (on-demand check, not a background SLA —
    # this architecture has no job queue; see ENGINEERING_AUDIT.md)
    # ------------------------------------------------------------------
    async def check_new_document(self, document_id: str) -> Optional[FailureWarning]:
        target = await self.repository.get_document(document_id)
        if not target:
            return None
        if isinstance(self.embedding_provider, HashingEmbeddingProvider):
            return FailureWarning(matched=False, risk_level="none", recommended_action="Real embeddings required for pattern matching.")
        target_vectors = [chunk.embedding for chunk in target.chunks if chunk.embedding]
        if not target_vectors:
            return FailureWarning(matched=False, risk_level="none")

        report = await self.patterns()
        if not report.available or not report.patterns:
            return FailureWarning(matched=False, risk_level="none")

        best_score = 0.0
        best_pattern: Optional[FailurePattern] = None
        for pattern in report.patterns:
            for document, chunk in await self.repository.iter_chunks():
                if document.metadata.filename not in pattern.document_filenames or not chunk.embedding:
                    continue
                score = max((cosine_similarity(tv, chunk.embedding) for tv in target_vectors), default=0.0)
                if score > best_score:
                    best_score = score
                    best_pattern = pattern

        if best_pattern is None or best_score < WARNING_THRESHOLD:
            return FailureWarning(matched=False, similarity_score=round(best_score, 4), risk_level="none")

        risk_level = "high" if best_score >= 0.9 else "medium"
        return FailureWarning(
            matched=True, matched_pattern_description=best_pattern.description,
            similarity_score=round(best_score, 4), risk_level=risk_level,
            recommended_action=f"Review against known pattern affecting {', '.join(best_pattern.affected_equipment) or 'related equipment'} before proceeding.",
        )
