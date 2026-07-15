"""Feature 3: Maintenance Intelligence & RCA Agent.

Reuses the existing knowledge-graph substrate (co_occurs/references edges from
`repository.build_graph_for_entity`) rather than building a new pipeline: RCA
is a depth-limited path search over that graph, scored with the same signal
style as retrieval (`scoring.py`), narrated with the same hedged-LLM +
deterministic-fallback pattern as the copilot (`copilot.py:_generate`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from ..config import Settings
from ..models import (
    Citation,
    DocumentChunk,
    DocumentResponse,
    EquipmentHealthReport,
    ExtractedEntity,
    FailureCluster,
    FailureClusterReport,
    GraphEdge,
    KnowledgeGraph,
    MaintenancePrediction,
    RCAChain,
    RCALink,
    RCAReport,
)
from ..repository import Repository, entity_node_key, normalize_graph_value
from .embeddings import EmbeddingProvider, HashingEmbeddingProvider, cosine_similarity
from .entities import IndustrialEntityExtractor
from .model_providers import DeterministicLLMProvider, LLMProvider
from .scoring import clamp01, confidence_from_score

# A chain must reach one of these node types to count as "root cause found".
# When the seed itself is a failure mode (e.g. from free-text /investigate),
# the search instead looks for concrete equipment/procedure/part root causes.
DEFAULT_TARGET_TYPES = {"FAILURE_MODE"}
FAILURE_SEEDED_TARGET_TYPES = {"EQUIPMENT_TAG", "PART_NUMBER", "PROCEDURE_ID"}

# Node types a chain may pass through. PERSON/DATE co-occur with equipment in
# real documents but aren't causal links themselves (a date isn't a cause) —
# excluding them keeps chains readable instead of routing through incidental
# entities. DOCUMENT nodes are pass-through hubs that connect entities across
# separate documents (the source of genuinely multi-hop, cross-document chains).
RCA_TRAVERSABLE_TYPES = {"EQUIPMENT_TAG", "FAILURE_MODE", "PART_NUMBER", "PROCEDURE_ID", "REGULATORY_REF", "DOCUMENT"}

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y")


@dataclass
class _Occurrence:
    document: DocumentResponse
    entity: ExtractedEntity
    chunk: Optional[DocumentChunk]


class RCAService:
    def __init__(
        self,
        repository: Repository,
        embedding_provider: EmbeddingProvider,
        settings: Optional[Settings] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.settings = settings or Settings()
        self.llm_provider = llm_provider or DeterministicLLMProvider()
        self.entity_extractor = IndustrialEntityExtractor()

    # ------------------------------------------------------------------
    # FR-18: RCA chain detection
    # ------------------------------------------------------------------
    async def rca(self, seed_type: str, seed_value: str) -> RCAReport:
        graph = await self.repository.build_graph_for_entity(seed_type, seed_value)
        documents = await self.repository.documents_for_entity(seed_type, seed_value)
        normalized_seed = normalize_graph_value(seed_type, seed_value)
        seed_id = entity_node_key(seed_type, normalized_seed)
        node_by_id = {node.id: node for node in graph.nodes}

        if not documents or seed_id not in node_by_id:
            return RCAReport(
                seed=seed_value,
                chains=[],
                narrative=f"No indexed evidence found for {seed_value}.",
                generation_status="skipped_no_evidence",
            )

        documents_by_id = {document.id: document for document in documents}
        occurrences = _index_occurrences(documents)
        adjacency = _adjacency(graph)
        node_types = {node.id: node.type for node in graph.nodes}
        target_types = (
            FAILURE_SEEDED_TARGET_TYPES if node_types.get(seed_id) == "FAILURE_MODE" else DEFAULT_TARGET_TYPES
        )

        raw_paths = _find_chains(seed_id, adjacency, node_types, self.settings.rca_max_hops, target_types)
        chains = [
            self._score_chain(path, node_by_id, node_types, occurrences, documents_by_id)
            for path in raw_paths
        ]
        chains = _dedupe_chains(chains)
        chains.sort(key=lambda chain: chain.chain_confidence, reverse=True)
        chains = chains[: self.settings.rca_top_chains]

        if not chains:
            return RCAReport(
                seed=seed_value,
                chains=[],
                narrative=(
                    f"No causal chain reaching a likely root cause was found for {seed_value} "
                    f"within {self.settings.rca_max_hops} hops."
                ),
                generation_status="skipped_no_evidence",
            )

        evidence_citations = [citation for chain in chains for link in chain.links for citation in link.citations]
        model_name = self.settings.default_answer_model
        try:
            narrative = await self.llm_provider.generate_answer(
                f"Investigate the potential root cause chain for {seed_value}.",
                evidence_citations,
                model_name,
                "rca_investigation",
            )
            generation_status = "llm"
        except Exception:
            narrative = _hedged_fallback_narrative(seed_value, chains)
            generation_status = "fallback"

        return RCAReport(
            seed=seed_value, chains=chains, narrative=narrative, generation_status=generation_status, model_used=model_name
        )

    def _score_chain(
        self,
        path: List[Tuple[str, str, GraphEdge]],
        node_by_id: dict,
        node_types: Dict[str, str],
        occurrences: Dict[str, List[_Occurrence]],
        documents_by_id: Dict[str, DocumentResponse],
    ) -> RCAChain:
        links: List[RCALink] = []
        doc_types_seen: Set[str] = set()
        docs_used: Set[str] = set()
        equipment_nodes_seen: Set[str] = set()

        for hop_index, (src, dst, edge) in enumerate(path, start=1):
            src_occ = occurrences.get(src, [])
            dst_occ = occurrences.get(dst, [])
            embed = _embedding_signal(src_occ, dst_occ)
            overlap = _overlap_signal(src_occ, dst_occ)
            temporal = _temporal_signal(src_occ, dst_occ, self.settings.rca_temporal_window_days)
            hop_doc_type = _hop_document_type(edge, documents_by_id)
            diversity = 1.0 if hop_doc_type and hop_doc_type not in doc_types_seen else 0.4
            if hop_doc_type:
                doc_types_seen.add(hop_doc_type)
            distance = 1.0 / hop_index
            score = clamp01(0.35 * embed + 0.25 * overlap + 0.15 * temporal + 0.10 * diversity + 0.15 * distance)

            links.append(
                RCALink(
                    source_id=src,
                    source_label=node_by_id[src].label,
                    target_id=dst,
                    target_label=node_by_id[dst].label,
                    relationship=edge.type,
                    link_confidence=round(score, 4),
                    signals={
                        "embedding_similarity": round(embed, 4),
                        "entity_overlap": round(overlap, 4),
                        "temporal_proximity": round(temporal, 4),
                        "doc_type_diversity": round(diversity, 4),
                        "graph_distance": round(distance, 4),
                    },
                    citations=_hop_citations(src_occ, dst_occ),
                )
            )
            docs_used.update(occ.document.id for occ in src_occ + dst_occ)
            if node_types.get(src) == "EQUIPMENT_TAG":
                equipment_nodes_seen.add(src)
            if node_types.get(dst) == "EQUIPMENT_TAG":
                equipment_nodes_seen.add(dst)

        chain_score = min((link.link_confidence for link in links), default=0.0)
        amplifications: List[str] = []
        target_node_id = path[-1][1]
        if node_types.get(target_node_id) == "FAILURE_MODE" and len(occurrences.get(target_node_id, [])) <= 2:
            chain_score = clamp01(chain_score * 1.08)
            amplifications.append("rare_failure_mode_boost")
        if len(equipment_nodes_seen) >= 2:
            chain_score = clamp01(chain_score * 1.15)
            amplifications.append("cross_system_boost")

        if len(docs_used) <= 1:
            chain_type = "direct_similarity"
        elif len(equipment_nodes_seen) >= 2:
            chain_type = "cross_domain_impact"
        else:
            chain_type = "indirect_ripple"

        return RCAChain(
            links=links,
            chain_confidence=round(chain_score, 4),
            confidence_label=confidence_from_score(chain_score),
            chain_type=chain_type,
            amplifications_applied=amplifications,
        )

    # ------------------------------------------------------------------
    # FR-18 companion: free-text incident seed resolution
    # ------------------------------------------------------------------
    async def investigate(self, incident_text: str) -> RCAReport:
        entities = self.entity_extractor.extract_from_text(incident_text, document_id="investigate", chunk_id="investigate")
        equipment = next((entity for entity in entities if entity.entity_type == "EQUIPMENT_TAG"), None)
        if equipment:
            return await self.rca("EQUIPMENT_TAG", equipment.text)
        failure = next((entity for entity in entities if entity.entity_type == "FAILURE_MODE"), None)
        if failure:
            return await self.rca("FAILURE_MODE", failure.text)
        return RCAReport(
            seed=incident_text,
            chains=[],
            narrative="No equipment tag or failure mode could be identified in the incident description.",
            generation_status="skipped_no_evidence",
        )

    # ------------------------------------------------------------------
    # FR-19: equipment health report
    # ------------------------------------------------------------------
    async def health(self, equipment_tag: str) -> EquipmentHealthReport:
        documents = await self.repository.documents_for_entity("EQUIPMENT_TAG", equipment_tag)
        if not documents:
            return EquipmentHealthReport(
                equipment_tag=equipment_tag,
                document_count=0,
                document_types=[],
                failure_history=[],
                open_procedures=[],
                related_parts=[],
                timeline=[],
                summary=f"No indexed documents reference {equipment_tag}.",
            )

        document_types = sorted({document.metadata.document_type for document in documents})
        failure_history = sorted({entity.text for document in documents for entity in document.entities if entity.entity_type == "FAILURE_MODE"})
        open_procedures = sorted({entity.text for document in documents for entity in document.entities if entity.entity_type == "PROCEDURE_ID"})
        related_parts = sorted({entity.text for document in documents for entity in document.entities if entity.entity_type == "PART_NUMBER"})

        timeline = [
            {"date": entity.text, "event": document.metadata.document_type, "source_document": document.metadata.filename}
            for document in documents
            for entity in document.entities
            if entity.entity_type == "DATE"
        ]
        timeline.sort(key=lambda row: _parse_date(row["date"]) or datetime.min)

        summary = (
            f"{equipment_tag} is referenced across {len(documents)} documents ({', '.join(document_types)}). "
            f"Failure history: {', '.join(failure_history) if failure_history else 'none recorded'}."
        )
        return EquipmentHealthReport(
            equipment_tag=equipment_tag,
            document_count=len(documents),
            document_types=document_types,
            failure_history=failure_history,
            open_procedures=open_procedures,
            related_parts=related_parts,
            timeline=timeline,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # FR-20: failure pattern clustering (Should have)
    # ------------------------------------------------------------------
    async def cluster_failures(self) -> FailureClusterReport:
        if isinstance(self.embedding_provider, HashingEmbeddingProvider):
            # ponytail: hashing embeddings aren't semantically meaningful, so
            # clustering them would produce noise dressed up as insight.
            # Upgrade path: enable IKI_EMBEDDING_BACKEND=sentence-transformers.
            return FailureClusterReport(available=False, reason="Real embeddings are required for clustering; the hashing fallback embeddings are not semantically meaningful.")
        try:
            import numpy as np
            from sklearn.cluster import DBSCAN
        except ImportError:
            return FailureClusterReport(available=False, reason="scikit-learn is not installed (see requirements-ml.txt).")

        vectors: List[List[float]] = []
        members: List[Tuple[DocumentResponse, DocumentChunk, List[ExtractedEntity]]] = []
        for document, chunk in await self.repository.iter_chunks():
            if not chunk.embedding:
                continue
            failure_entities = [entity for entity in await self.repository.entities_for_chunk(chunk.id) if entity.entity_type == "FAILURE_MODE"]
            if not failure_entities:
                continue
            vectors.append(chunk.embedding)
            members.append((document, chunk, failure_entities))

        if len(vectors) < 2:
            return FailureClusterReport(available=True, clusters=[])

        # eps calibrated against real BAAI/bge-base-en-v1.5 cosine distances on
        # the augmented corpus: genuinely related failure chunks (same
        # equipment story, different documents) sit at 0.11-0.20; unrelated
        # equipment stories start at 0.223. An earlier, untested eps=0.35
        # merged the entire corpus into one cluster (verified live against
        # real embeddings, not assumed). ponytail: single-corpus calibration;
        # a larger/denser real corpus may need this re-derived.
        labels = DBSCAN(eps=0.21, min_samples=2, metric="cosine").fit_predict(np.array(vectors))
        grouped: Dict[int, List[int]] = defaultdict(list)
        for index, label in enumerate(labels):
            if label == -1:
                continue
            grouped[int(label)].append(index)

        clusters = [
            FailureCluster(
                cluster_id=cluster_id,
                failure_terms=sorted({entity.text for i in indices for entity in members[i][2]}),
                member_count=len(indices),
                representative_excerpt=members[indices[0]][1].content[:200],
                document_filenames=sorted({members[i][0].metadata.filename for i in indices}),
            )
            for cluster_id, indices in grouped.items()
        ]
        return FailureClusterReport(available=True, clusters=clusters)

    # ------------------------------------------------------------------
    # FR-21: predictive maintenance recommendations (Nice to have)
    # ------------------------------------------------------------------
    async def predictions(self) -> List[MaintenancePrediction]:
        graph = await self.repository.build_graph()
        equipment_tags = sorted(
            {node.metadata.get("normalized_text", node.label) for node in graph.nodes if node.type == "EQUIPMENT_TAG"}
        )
        results: List[MaintenancePrediction] = []
        for tag in equipment_tags:
            report = await self.health(tag)
            if not report.failure_history:
                continue
            urgency = "high" if len(report.failure_history) >= 3 else "medium" if len(report.failure_history) >= 2 else "low"
            results.append(
                MaintenancePrediction(
                    equipment_tag=tag,
                    recommendation=f"Schedule inspection for {tag}; recurring failure indicators present ({', '.join(report.failure_history)}).",
                    urgency=urgency,
                    justification=report.failure_history,
                )
            )
        urgency_rank = {"high": 0, "medium": 1, "low": 2}
        results.sort(key=lambda prediction: urgency_rank[prediction.urgency])
        return results


# ----------------------------------------------------------------------
# Graph traversal helpers
# ----------------------------------------------------------------------


def _index_occurrences(documents: List[DocumentResponse]) -> Dict[str, List[_Occurrence]]:
    index: Dict[str, List[_Occurrence]] = defaultdict(list)
    for document in documents:
        chunks_by_id = {chunk.id: chunk for chunk in document.chunks}
        for entity in document.entities:
            node_id = entity_node_key(entity.entity_type, entity.normalized_text)
            index[node_id].append(_Occurrence(document=document, entity=entity, chunk=chunks_by_id.get(entity.chunk_id)))
    return index


def _adjacency(graph: KnowledgeGraph) -> Dict[str, List[Tuple[str, GraphEdge]]]:
    adjacency: Dict[str, List[Tuple[str, GraphEdge]]] = defaultdict(list)
    for edge in graph.edges:
        adjacency[edge.source].append((edge.target, edge))
        adjacency[edge.target].append((edge.source, edge))
    return adjacency


def _dedupe_chains(chains: List[RCAChain]) -> List[RCAChain]:
    """Multiple co_occurs edges can connect the same node pair (one per shared
    document), producing chains with identical hop labels but different
    backing edges. Keep the highest-scoring instance per label sequence so the
    top-K isn't crowded out by near-duplicates."""
    best: Dict[Tuple[str, ...], RCAChain] = {}
    for chain in chains:
        shape = tuple(link.source_label for link in chain.links) + (chain.links[-1].target_label if chain.links else "",)
        existing = best.get(shape)
        if existing is None or chain.chain_confidence > existing.chain_confidence:
            best[shape] = chain
    return list(best.values())


def _find_chains(
    seed_id: str,
    adjacency: Dict[str, List[Tuple[str, GraphEdge]]],
    node_types: Dict[str, str],
    max_hops: int,
    target_types: Set[str],
) -> List[List[Tuple[str, str, GraphEdge]]]:
    """Hop-capped DFS collecting every simple path from the seed to a node of
    one of `target_types`. ponytail: fine at per-equipment subgraph scale
    (tens of nodes); switch to networkx if subgraphs grow large."""
    results: List[List[Tuple[str, str, GraphEdge]]] = []

    def dfs(current: str, visited: Set[str], path: List[Tuple[str, str, GraphEdge]]) -> None:
        if path and node_types.get(current) in target_types:
            results.append(list(path))
        if len(path) >= max_hops:
            return
        for neighbor, edge in adjacency.get(current, []):
            if neighbor in visited or node_types.get(neighbor) not in RCA_TRAVERSABLE_TYPES:
                continue
            dfs(neighbor, visited | {neighbor}, path + [(current, neighbor, edge)])

    dfs(seed_id, {seed_id}, [])
    return results


# ----------------------------------------------------------------------
# Signal scoring (ARCHITECTURE.md §5.3 weights: 0.35/0.25/0.15/0.10/0.15)
# ----------------------------------------------------------------------


def _embedding_signal(src_occ: List[_Occurrence], dst_occ: List[_Occurrence]) -> float:
    best: Optional[float] = None
    for a in src_occ:
        if not a.chunk or not a.chunk.embedding:
            continue
        for b in dst_occ:
            if not b.chunk or not b.chunk.embedding:
                continue
            score = cosine_similarity(a.chunk.embedding, b.chunk.embedding)
            best = score if best is None else max(best, score)
    return clamp01(best) if best is not None else 0.5


def _overlap_signal(src_occ: List[_Occurrence], dst_occ: List[_Occurrence]) -> float:
    src_docs = {occ.document.id for occ in src_occ}
    dst_docs = {occ.document.id for occ in dst_occ}
    union = src_docs | dst_docs
    if not union:
        return 0.0
    return clamp01(len(src_docs & dst_docs) / len(union))


def _parse_date(raw: str) -> Optional[datetime]:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _occurrence_dates(occurrences: List[_Occurrence]) -> List[datetime]:
    dates: List[datetime] = []
    for occ in occurrences:
        for entity in occ.document.entities:
            if entity.entity_type == "DATE":
                parsed = _parse_date(entity.text)
                if parsed:
                    dates.append(parsed)
    return dates


def _temporal_signal(src_occ: List[_Occurrence], dst_occ: List[_Occurrence], window_days: int) -> float:
    src_dates = _occurrence_dates(src_occ)
    dst_dates = _occurrence_dates(dst_occ)
    if not src_dates or not dst_dates or window_days <= 0:
        return 0.5
    best_gap = min(abs((a - b).days) for a in src_dates for b in dst_dates)
    return clamp01(1 - (best_gap / window_days))


def _hop_document_type(edge: GraphEdge, documents_by_id: Dict[str, DocumentResponse]) -> Optional[str]:
    document_id = edge.metadata.get("document_id")
    document = documents_by_id.get(document_id) if document_id else None
    return document.metadata.document_type if document else None


def _hop_citations(src_occ: List[_Occurrence], dst_occ: List[_Occurrence]) -> List[Citation]:
    citations: Dict[str, Citation] = {}
    for occ in src_occ + dst_occ:
        if not occ.chunk:
            continue
        citations[occ.document.id] = Citation(
            document_id=occ.document.id,
            filename=occ.document.metadata.filename,
            chunk_id=occ.chunk.id,
            page_number=occ.chunk.page_number,
            relevance_score=1.0,
            excerpt=occ.chunk.content[:320],
        )
    return list(citations.values())


def _hedged_fallback_narrative(seed: str, chains: List[RCAChain]) -> str:
    top = chains[0]
    hop_labels = [top.links[0].source_label] + [link.target_label for link in top.links]
    return (
        f"Potential root-cause chain for {seed} (hedged — not confirmed): "
        + " -> ".join(hop_labels)
        + f". Chain confidence: {top.confidence_label} ({top.chain_confidence:.2f}). "
        "Review the cited source documents before making an operational decision."
    )
