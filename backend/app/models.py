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
