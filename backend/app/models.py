from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    filename: str
    content_type: str
    document_type: str
    page_count: int = 1
    content_hash: str
    byte_size: int
    storage_uri: str
    created_at: str = ""


class DocumentChunk(BaseModel):
    id: str
    document_id: str
    chunk_index: int
    content: str
    page_number: int = 1
    section_title: Optional[str] = None
    embedding: List[float] = Field(default_factory=list)


class ExtractedEntity(BaseModel):
    id: str
    document_id: str
    chunk_id: str
    entity_type: str
    text: str
    normalized_text: str
    confidence: float = Field(ge=0.0, le=1.0)


class DocumentResponse(BaseModel):
    id: str
    status: str
    duplicate_of: Optional[str] = None
    metadata: DocumentMetadata
    chunks: List[DocumentChunk]
    entities: List[ExtractedEntity]


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    metadata: Dict[str, str] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str
    weight: float = 1.0
    metadata: Dict[str, str] = Field(default_factory=dict)


class KnowledgeGraph(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class Citation(BaseModel):
    document_id: str
    filename: str
    chunk_id: str
    page_number: int
    relevance_score: float
    excerpt: str


class CopilotQuery(BaseModel):
    question: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=10)


class CopilotResponse(BaseModel):
    answer: str
    confidence: str
    citations: List[Citation]
    retrieved_chunk_count: int
    model_used: Optional[str] = None
    cache_status: str = "miss"
    query_type: str = "simple_evidence_query"
    generation_status: str = "deterministic"
    rerank_status: str = "not_used"


class AuditEvent(BaseModel):
    method: str
    path: str
    status_code: int
    duration_ms: float
    client_host: str
    user_agent: str = ""


class SemanticCacheEntry(BaseModel):
    normalized_query: str
    query_embedding: List[float]
    model_name: str
    prompt_version: str
    corpus_version: int
    citation_hash: str
    answer: str
    confidence: str
    citations: List[Citation]
    retrieved_chunk_count: int
    query_type: str
    cache_metadata: Dict[str, str] = Field(default_factory=dict)


class RCALink(BaseModel):
    """One hop in a causal chain, e.g. `equipment_tag:P101A -> failure_mode:bearing failure`."""

    source_id: str
    source_label: str
    target_id: str
    target_label: str
    relationship: str
    link_confidence: float = Field(ge=0.0, le=1.0)
    signals: Dict[str, float] = Field(default_factory=dict)
    citations: List[Citation] = Field(default_factory=list)


class RCAChain(BaseModel):
    links: List[RCALink]
    chain_confidence: float = Field(ge=0.0, le=1.0)
    confidence_label: str
    chain_type: str  # direct_similarity | indirect_ripple | cross_domain_impact
    amplifications_applied: List[str] = Field(default_factory=list)


class RCAReport(BaseModel):
    seed: str
    chains: List[RCAChain]
    narrative: str
    generation_status: str = "deterministic"
    model_used: Optional[str] = None


class EquipmentHealthReport(BaseModel):
    equipment_tag: str
    document_count: int
    document_types: List[str]
    failure_history: List[str]
    open_procedures: List[str]
    related_parts: List[str]
    timeline: List[Dict[str, str]]  # [{date, event, source_document}]
    summary: str


class FailureCluster(BaseModel):
    cluster_id: int
    failure_terms: List[str]
    member_count: int
    representative_excerpt: str
    document_filenames: List[str]


class FailureClusterReport(BaseModel):
    available: bool
    reason: Optional[str] = None
    clusters: List[FailureCluster] = Field(default_factory=list)


class MaintenancePrediction(BaseModel):
    equipment_tag: str
    recommendation: str
    urgency: str  # low | medium | high
    justification: List[str]  # historical failures cited as evidence


class RequirementResult(BaseModel):
    requirement_id: str
    requirement_text: str
    status: str  # compliant | partial | gap
    coverage_score: float = Field(ge=0.0, le=1.0)
    citations: List[Citation] = Field(default_factory=list)
    action_needed: str = ""


class RegulationCompliance(BaseModel):
    regulation: str
    title: str
    requirements: List[RequirementResult]
    coverage_pct: float = Field(ge=0.0, le=100.0)
    status_counts: Dict[str, int] = Field(default_factory=dict)  # {compliant, partial, gap}


class ComplianceStatus(BaseModel):
    regulations: List[RegulationCompliance]
    overall_coverage_pct: float = Field(ge=0.0, le=100.0)
    framework_disclaimer: str


class ComplianceGap(BaseModel):
    regulation: str
    requirement_id: str
    requirement_text: str
    status: str  # partial | gap
    evidence: List[Citation] = Field(default_factory=list)
    action_needed: str


class EvidencePackage(BaseModel):
    regulation: str
    title: str
    requirements: List[RequirementResult]
    coverage_pct: float = Field(ge=0.0, le=100.0)
    summary: str
    generation_status: str = "deterministic"
    disclaimer: str


class EquipmentCompliance(BaseModel):
    equipment_tag: str
    applicable_regulations: List[str]
    results: List[RequirementResult]


class IncidentAnalysis(BaseModel):
    document_id: str
    filename: str
    contributing_factors: List[str]
    affected_equipment: List[str]
    root_cause_summary: str
    generation_status: str = "deterministic"


class FailurePattern(BaseModel):
    cluster_id: int
    description: str
    frequency: int
    affected_equipment: List[str]
    severity_trend: str  # escalating | recurring | unclassified
    document_filenames: List[str]


class PatternReport(BaseModel):
    available: bool
    reason: Optional[str] = None
    patterns: List[FailurePattern] = Field(default_factory=list)


class SimilarIncident(BaseModel):
    document_id: str
    filename: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    lessons_learned: str


class SimilarIncidentReport(BaseModel):
    seed_document_id: str
    seed_filename: str
    similar_incidents: List[SimilarIncident] = Field(default_factory=list)


class FailureWarning(BaseModel):
    matched: bool
    matched_pattern_description: Optional[str] = None
    similarity_score: float = Field(ge=0.0, le=1.0, default=0.0)
    risk_level: str = "none"  # none | low | medium | high
    recommended_action: str = ""


class DocumentTypeCount(BaseModel):
    document_type: str
    count: int


class DocumentStats(BaseModel):
    total_documents: int
    by_type: List[DocumentTypeCount]
    by_status: Dict[str, int]
    earliest_ingested: Optional[str] = None
    latest_ingested: Optional[str] = None


class EndpointUsage(BaseModel):
    path: str
    request_count: int
    avg_duration_ms: float
    error_count: int


class UsageStats(BaseModel):
    total_requests: int
    total_errors: int
    llm_invoking_requests: int
    by_endpoint: List[EndpointUsage]
    note: str


class PipelineStatus(BaseModel):
    documents_indexed: int
    documents_duplicate: int
    upload_errors_recent: int
    pending_reprocessing: int
    note: str


class IngestionFailure(BaseModel):
    id: str
    filename: str
    content_type: str
    byte_size: int
    storage_uri: str
    error_message: str
    attempts: int = 1
    created_at: str = ""
    last_attempt_at: str = ""


class AdminOverview(BaseModel):
    documents: DocumentStats
    usage: UsageStats
    pipeline: PipelineStatus


class DocumentSummary(BaseModel):
    id: str
    filename: str
    document_type: str
    status: str
    byte_size: int
    created_at: str
