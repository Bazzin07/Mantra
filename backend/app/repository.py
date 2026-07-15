import json
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Protocol, Set, Tuple

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db_models import (
    AuditEventRecord,
    CorpusStateRecord,
    DocumentChunkRecord,
    DocumentRecord,
    ExtractedEntityRecord,
    SemanticCacheRecord,
)
from .models import (
    AuditEvent,
    Citation,
    DocumentChunk,
    DocumentMetadata,
    DocumentResponse,
    ExtractedEntity,
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    SemanticCacheEntry,
)
from .services.embeddings import cosine_similarity
from .services.entities import EQUIPMENT_NOUN_PATTERN, canonical_equipment_tag, normalize_entity
from .services.scoring import bm25_lite, combine_signals, entity_fraction, normalize_lexical


class Repository(Protocol):
    async def add_document(self, document: DocumentResponse) -> DocumentResponse: ...

    async def find_duplicate(self, content_hash: str) -> Optional[DocumentResponse]: ...

    async def get_document(self, document_id: str) -> Optional[DocumentResponse]: ...

    async def count_documents(self) -> int: ...

    async def add_audit_event(self, event: AuditEvent) -> None: ...

    async def get_corpus_version(self) -> int: ...

    async def get_exact_semantic_cache(
        self,
        normalized_query: str,
        model_name: str,
        prompt_version: str,
        corpus_version: int,
    ) -> Optional[SemanticCacheEntry]: ...

    async def find_semantic_cache(
        self,
        query_embedding: List[float],
        model_name: str,
        prompt_version: str,
        corpus_version: int,
        threshold: float,
    ) -> Optional[SemanticCacheEntry]: ...

    async def upsert_semantic_cache(self, entry: SemanticCacheEntry) -> None: ...

    async def iter_chunks(self) -> List[Tuple[DocumentResponse, DocumentChunk]]: ...

    async def entities_for_chunk(self, chunk_id: str) -> List[ExtractedEntity]: ...

    async def search_chunks(
        self,
        query_text: str,
        query_tokens: List[str],
        query_embedding: List[float],
        query_entity_keys: Set[Tuple[str, str]],
        limit: int,
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]: ...

    async def build_graph(self) -> KnowledgeGraph: ...

    async def build_graph_for_entity(self, entity_type: str, raw_value: str) -> KnowledgeGraph: ...

    async def documents_for_entity(self, entity_type: str, raw_value: str) -> List[DocumentResponse]: ...


class InMemoryRepository:
    """Test/development repository with the same async contract as durable storage."""

    def __init__(self) -> None:
        self.documents: Dict[str, DocumentResponse] = {}
        self.content_hash_to_document_id: Dict[str, str] = {}
        self.corpus_version = 0
        self.semantic_cache: List[SemanticCacheEntry] = []

    async def add_document(self, document: DocumentResponse) -> DocumentResponse:
        self.documents[document.id] = document
        self.content_hash_to_document_id[document.metadata.content_hash] = document.id
        self.corpus_version += 1
        return document

    async def find_duplicate(self, content_hash: str) -> Optional[DocumentResponse]:
        document_id = self.content_hash_to_document_id.get(content_hash)
        if not document_id:
            return None
        return self.documents.get(document_id)

    async def get_document(self, document_id: str) -> Optional[DocumentResponse]:
        return self.documents.get(document_id)

    async def count_documents(self) -> int:
        return len(self.documents)

    async def add_audit_event(self, event: AuditEvent) -> None:
        return None

    async def get_corpus_version(self) -> int:
        return self.corpus_version

    async def get_exact_semantic_cache(
        self,
        normalized_query: str,
        model_name: str,
        prompt_version: str,
        corpus_version: int,
    ) -> Optional[SemanticCacheEntry]:
        for entry in self.semantic_cache:
            if (
                entry.normalized_query == normalized_query
                and entry.model_name == model_name
                and entry.prompt_version == prompt_version
                and entry.corpus_version == corpus_version
            ):
                return entry
        return None

    async def find_semantic_cache(
        self,
        query_embedding: List[float],
        model_name: str,
        prompt_version: str,
        corpus_version: int,
        threshold: float,
    ) -> Optional[SemanticCacheEntry]:
        best_score = threshold
        best_entry: Optional[SemanticCacheEntry] = None
        for entry in self.semantic_cache:
            if entry.model_name != model_name or entry.prompt_version != prompt_version or entry.corpus_version != corpus_version:
                continue
            score = cosine_similarity(query_embedding, entry.query_embedding)
            if score >= best_score:
                best_score = score
                best_entry = entry
        return best_entry

    async def upsert_semantic_cache(self, entry: SemanticCacheEntry) -> None:
        self.semantic_cache = [
            cached
            for cached in self.semantic_cache
            if not (
                cached.normalized_query == entry.normalized_query
                and cached.model_name == entry.model_name
                and cached.prompt_version == entry.prompt_version
                and cached.corpus_version == entry.corpus_version
            )
        ]
        self.semantic_cache.append(entry)

    async def iter_chunks(self) -> List[Tuple[DocumentResponse, DocumentChunk]]:
        return [(document, chunk) for document in self.documents.values() for chunk in document.chunks]

    async def entities_for_chunk(self, chunk_id: str) -> List[ExtractedEntity]:
        matches: List[ExtractedEntity] = []
        for document in self.documents.values():
            matches.extend([entity for entity in document.entities if entity.chunk_id == chunk_id])
        return matches

    async def search_chunks(
        self,
        query_text: str,
        query_tokens: List[str],
        query_embedding: List[float],
        query_entity_keys: Set[Tuple[str, str]],
        limit: int,
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
        return await application_level_search(self, query_text, query_tokens, query_embedding, query_entity_keys, limit)

    async def build_graph(self) -> KnowledgeGraph:
        return build_graph_for_documents(list(self.documents.values()))

    async def build_graph_for_entity(self, entity_type: str, raw_value: str) -> KnowledgeGraph:
        normalized = normalize_graph_value(entity_type, raw_value)
        documents = await self.documents_for_entity(entity_type, raw_value)
        return build_graph_for_documents(documents, required_entity=(entity_type, normalized))

    async def documents_for_entity(self, entity_type: str, raw_value: str) -> List[DocumentResponse]:
        normalized = normalize_graph_value(entity_type, raw_value)
        return [
            document
            for document in self.documents.values()
            if any(
                entity.entity_type == entity_type and entity.normalized_text == normalized
                for entity in document.entities
            )
        ]


class SqlAlchemyRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def add_document(self, document: DocumentResponse) -> DocumentResponse:
        async with self.session_factory() as session:
            record = DocumentRecord(
                id=document.id,
                filename=document.metadata.filename,
                content_type=document.metadata.content_type,
                document_type=document.metadata.document_type,
                page_count=document.metadata.page_count,
                content_hash=document.metadata.content_hash,
                byte_size=document.metadata.byte_size,
                storage_uri=document.metadata.storage_uri,
                status=document.status,
            )
            session.add(record)
            chunk_records = [
                DocumentChunkRecord(
                    id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                    embedding_json=json.dumps(chunk.embedding) if chunk.embedding else None,
                )
                for chunk in document.chunks
            ]
            session.add_all(chunk_records)
            session.add_all(
                ExtractedEntityRecord(
                    id=entity.id,
                    document_id=entity.document_id,
                    chunk_id=entity.chunk_id,
                    entity_type=entity.entity_type,
                    text=entity.text,
                    normalized_text=entity.normalized_text,
                    confidence=entity.confidence,
                )
                for entity in document.entities
            )
            await session.flush()
            if session.get_bind().dialect.name == "postgresql":
                for chunk in document.chunks:
                    if not chunk.embedding:
                        continue
                    embedding_literal = "[" + ",".join(str(float(value)) for value in chunk.embedding) + "]"
                    await session.execute(
                        text("UPDATE document_chunks SET embedding = CAST(:embedding AS vector) WHERE id = :chunk_id"),
                        {"embedding": embedding_literal, "chunk_id": chunk.id},
                    )
            await ensure_corpus_state(session)
            await session.execute(text("UPDATE corpus_state SET version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"))
            await session.commit()
        return document

    async def find_duplicate(self, content_hash: str) -> Optional[DocumentResponse]:
        query = select(DocumentRecord).where(DocumentRecord.content_hash == content_hash)
        return await self._one_document(query)

    async def get_document(self, document_id: str) -> Optional[DocumentResponse]:
        query = select(DocumentRecord).where(DocumentRecord.id == document_id)
        return await self._one_document(query)

    async def count_documents(self) -> int:
        async with self.session_factory() as session:
            result = await session.execute(select(func.count()).select_from(DocumentRecord))
            return int(result.scalar_one())

    async def add_audit_event(self, event: AuditEvent) -> None:
        async with self.session_factory() as session:
            session.add(
                AuditEventRecord(
                    method=event.method,
                    path=event.path,
                    status_code=event.status_code,
                    duration_ms=event.duration_ms,
                    client_host=event.client_host,
                    user_agent=event.user_agent,
                )
            )
            await session.commit()

    async def get_corpus_version(self) -> int:
        async with self.session_factory() as session:
            await ensure_corpus_state(session)
            result = await session.execute(select(CorpusStateRecord.version).where(CorpusStateRecord.id == 1))
            await session.commit()
            return int(result.scalar_one())

    async def get_exact_semantic_cache(
        self,
        normalized_query: str,
        model_name: str,
        prompt_version: str,
        corpus_version: int,
    ) -> Optional[SemanticCacheEntry]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(SemanticCacheRecord).where(
                    SemanticCacheRecord.normalized_query == normalized_query,
                    SemanticCacheRecord.model_name == model_name,
                    SemanticCacheRecord.prompt_version == prompt_version,
                    SemanticCacheRecord.corpus_version == corpus_version,
                )
            )
            record = result.scalars().first()
            if record:
                await increment_cache_hit(session, record.id)
                await session.commit()
                return semantic_cache_record_to_model(record)
            return None

    async def find_semantic_cache(
        self,
        query_embedding: List[float],
        model_name: str,
        prompt_version: str,
        corpus_version: int,
        threshold: float,
    ) -> Optional[SemanticCacheEntry]:
        async with self.session_factory() as session:
            bind = session.get_bind()
            if bind and bind.dialect.name == "postgresql":
                embedding_literal = "[" + ",".join(str(float(value)) for value in query_embedding) + "]"
                result = await session.execute(
                    text(
                        """
                        SELECT id
                        FROM semantic_query_cache
                        WHERE model_name = :model_name
                          AND prompt_version = :prompt_version
                          AND corpus_version = :corpus_version
                          AND query_embedding IS NOT NULL
                          AND 1.0 - (query_embedding <=> CAST(:embedding AS vector)) >= :threshold
                        ORDER BY query_embedding <=> CAST(:embedding AS vector)
                        LIMIT 1
                        """
                    ),
                    {
                        "model_name": model_name,
                        "prompt_version": prompt_version,
                        "corpus_version": corpus_version,
                        "embedding": embedding_literal,
                        "threshold": threshold,
                    },
                )
                cache_id = result.scalar()
                if cache_id is not None:
                    record_result = await session.execute(
                        select(SemanticCacheRecord).where(SemanticCacheRecord.id == int(cache_id))
                    )
                    record = record_result.scalars().one()
                    await increment_cache_hit(session, record.id)
                    await session.commit()
                    return semantic_cache_record_to_model(record)

            result = await session.execute(
                select(SemanticCacheRecord).where(
                    SemanticCacheRecord.model_name == model_name,
                    SemanticCacheRecord.prompt_version == prompt_version,
                    SemanticCacheRecord.corpus_version == corpus_version,
                )
            )
            best_score = threshold
            best_record: Optional[SemanticCacheRecord] = None
            for record in result.scalars().all():
                score = cosine_similarity(query_embedding, json.loads(record.query_embedding_json))
                if score >= best_score:
                    best_score = score
                    best_record = record
            if best_record:
                await increment_cache_hit(session, best_record.id)
                await session.commit()
                return semantic_cache_record_to_model(best_record)
            return None

    async def upsert_semantic_cache(self, entry: SemanticCacheEntry) -> None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(SemanticCacheRecord).where(
                    SemanticCacheRecord.normalized_query == entry.normalized_query,
                    SemanticCacheRecord.model_name == entry.model_name,
                    SemanticCacheRecord.prompt_version == entry.prompt_version,
                    SemanticCacheRecord.corpus_version == entry.corpus_version,
                )
            )
            record = result.scalars().first()
            if record is None:
                record = SemanticCacheRecord(normalized_query=entry.normalized_query)
                session.add(record)
            record.query_embedding_json = json.dumps(entry.query_embedding)
            record.model_name = entry.model_name
            record.prompt_version = entry.prompt_version
            record.corpus_version = entry.corpus_version
            record.citation_hash = entry.citation_hash
            record.answer = entry.answer
            record.confidence = entry.confidence
            record.citations_json = json.dumps([citation.model_dump() for citation in entry.citations])
            record.retrieved_chunk_count = entry.retrieved_chunk_count
            record.query_type = entry.query_type
            record.cache_metadata_json = json.dumps(entry.cache_metadata)
            await session.flush()
            if session.get_bind().dialect.name == "postgresql":
                embedding_literal = "[" + ",".join(str(float(value)) for value in entry.query_embedding) + "]"
                await session.execute(
                    text("UPDATE semantic_query_cache SET query_embedding = CAST(:embedding AS vector) WHERE id = :cache_id"),
                    {"embedding": embedding_literal, "cache_id": record.id},
                )
            await session.commit()

    async def iter_chunks(self) -> List[Tuple[DocumentResponse, DocumentChunk]]:
        documents = await self._all_documents(select(DocumentRecord))
        return [(document, chunk) for document in documents for chunk in document.chunks]

    async def entities_for_chunk(self, chunk_id: str) -> List[ExtractedEntity]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(ExtractedEntityRecord).where(ExtractedEntityRecord.chunk_id == chunk_id)
            )
            return [entity_record_to_model(record) for record in result.scalars().all()]

    async def search_chunks(
        self,
        query_text: str,
        query_tokens: List[str],
        query_embedding: List[float],
        query_entity_keys: Set[Tuple[str, str]],
        limit: int,
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
        async with self.session_factory() as session:
            bind = session.get_bind()
            if bind and bind.dialect.name == "postgresql":
                native_results = await self._postgres_hybrid_search(
                    session,
                    query_text,
                    query_tokens,
                    query_embedding,
                    query_entity_keys,
                    limit,
                )
                if native_results:
                    return native_results
        return await application_level_search(self, query_text, query_tokens, query_embedding, query_entity_keys, limit)

    async def build_graph(self) -> KnowledgeGraph:
        return build_graph_for_documents(await self._all_documents(select(DocumentRecord)))

    async def build_graph_for_entity(self, entity_type: str, raw_value: str) -> KnowledgeGraph:
        normalized = normalize_graph_value(entity_type, raw_value)
        documents = await self.documents_for_entity(entity_type, raw_value)
        return build_graph_for_documents(documents, required_entity=(entity_type, normalized))

    async def documents_for_entity(self, entity_type: str, raw_value: str) -> List[DocumentResponse]:
        normalized = normalize_graph_value(entity_type, raw_value)
        async with self.session_factory() as session:
            result = await session.execute(
                select(DocumentRecord)
                .join(ExtractedEntityRecord)
                .where(
                    ExtractedEntityRecord.entity_type == entity_type,
                    ExtractedEntityRecord.normalized_text == normalized,
                )
                .distinct()
            )
            return [document_record_to_model(record) for record in result.scalars().unique().all()]

    async def _one_document(self, query: Select[Tuple[DocumentRecord]]) -> Optional[DocumentResponse]:
        documents = await self._all_documents(query)
        return documents[0] if documents else None

    async def _all_documents(self, query: Select[Tuple[DocumentRecord]]) -> List[DocumentResponse]:
        async with self.session_factory() as session:
            result = await session.execute(query)
            return [document_record_to_model(record) for record in result.scalars().unique().all()]

    async def _postgres_hybrid_search(
        self,
        session: AsyncSession,
        query_text: str,
        query_tokens: List[str],
        query_embedding: List[float],
        query_entity_keys: Set[Tuple[str, str]],
        limit: int,
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
        """PostgreSQL-native hybrid retrieval.

        The SQL layer generates candidates fast using pgvector + tsvector, then
        the same unified ``scoring`` helpers used by the application fallback
        combine the signals. This keeps the final score on the shared [0, 1]
        scale and adds the entity-overlap and exact-phrase signals that the pure
        SQL ranking used to omit, so PostgreSQL and SQLite rank and score
        consistently.
        """
        embedding_literal = "[" + ",".join(str(float(value)) for value in query_embedding) + "]"
        query_terms = " ".join(filter_search_tokens(query_tokens)) or query_text
        ts_terms = build_or_tsquery(query_tokens)
        result = await session.execute(
            text(
                """
                WITH query AS (
                    SELECT
                        to_tsquery('english', :ts_terms) AS ts_query,
                        CAST(:embedding AS vector) AS query_embedding
                )
                SELECT
                    d.id AS document_id,
                    d.filename,
                    d.content_type,
                    d.document_type,
                    d.page_count,
                    d.content_hash,
                    d.byte_size,
                    d.storage_uri,
                    d.status,
                    c.id AS chunk_id,
                    c.chunk_index,
                    c.content,
                    c.page_number,
                    c.section_title,
                    c.embedding_json,
                    CASE
                        WHEN c.ts_content @@ query.ts_query THEN ts_rank(c.ts_content, query.ts_query)
                        ELSE 0.0
                    END AS lexical_score,
                    CASE
                        WHEN c.embedding IS NULL THEN 0.0
                        ELSE 1.0 - (c.embedding <=> query.query_embedding)
                    END AS vector_score
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                CROSS JOIN query
                WHERE
                    c.ts_content @@ query.ts_query
                    OR c.content ILIKE :query_like
                    OR (
                        c.embedding IS NOT NULL
                        AND 1.0 - (c.embedding <=> query.query_embedding) > 0.05
                    )
                ORDER BY ((0.60 * COALESCE(ts_rank(c.ts_content, query.ts_query), 0.0)) +
                          (0.30 * CASE WHEN c.embedding IS NULL THEN 0.0 ELSE 1.0 - (c.embedding <=> query.query_embedding) END)) DESC
                LIMIT :limit
                """
            ),
            {
                "ts_terms": ts_terms,
                "query_like": f"%{query_terms}%",
                "embedding": embedding_literal,
                "limit": limit,
            },
        )
        rows = list(result.mappings())
        if not rows:
            return []

        chunk_entity_keys = await self._entity_keys_for_chunks(session, [row["chunk_id"] for row in rows])
        total_query_entities = len(query_entity_keys)
        query_lower = query_text.lower()
        normalized_lexical = normalize_lexical([float(row["lexical_score"]) for row in rows])

        ranked: List[Tuple[float, DocumentResponse, DocumentChunk]] = []
        for row, lexical_norm in zip(rows, normalized_lexical):
            document = DocumentResponse(
                id=row["document_id"],
                status=row["status"],
                metadata=DocumentMetadata(
                    filename=row["filename"],
                    content_type=row["content_type"],
                    document_type=row["document_type"],
                    page_count=row["page_count"],
                    content_hash=row["content_hash"],
                    byte_size=row["byte_size"],
                    storage_uri=row["storage_uri"],
                ),
                chunks=[],
                entities=[],
            )
            chunk = DocumentChunk(
                id=row["chunk_id"],
                document_id=row["document_id"],
                chunk_index=row["chunk_index"],
                content=row["content"],
                page_number=row["page_number"],
                section_title=row["section_title"],
                embedding=json.loads(row["embedding_json"]) if row["embedding_json"] else [],
            )
            matched = len(query_entity_keys.intersection(chunk_entity_keys.get(row["chunk_id"], set())))
            entity_frac = entity_fraction(matched, total_query_entities)
            phrase = 1.0 if query_lower and query_lower in (row["content"] or "").lower() else 0.0
            score = combine_signals(lexical_norm, float(row["vector_score"]), entity_frac, phrase)
            ranked.append((score, document, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[:limit]

    async def _entity_keys_for_chunks(
        self,
        session: AsyncSession,
        chunk_ids: List[str],
    ) -> Dict[str, Set[Tuple[str, str]]]:
        if not chunk_ids:
            return {}
        result = await session.execute(
            select(
                ExtractedEntityRecord.chunk_id,
                ExtractedEntityRecord.entity_type,
                ExtractedEntityRecord.normalized_text,
            ).where(ExtractedEntityRecord.chunk_id.in_(chunk_ids))
        )
        keys: Dict[str, Set[Tuple[str, str]]] = {}
        for chunk_id, entity_type, normalized_text in result.all():
            keys.setdefault(chunk_id, set()).add((entity_type, normalized_text))
        return keys


def document_record_to_model(record: DocumentRecord) -> DocumentResponse:
    return DocumentResponse(
        id=record.id,
        status=record.status,
        metadata=DocumentMetadata(
            filename=record.filename,
            content_type=record.content_type,
            document_type=record.document_type,
            page_count=record.page_count,
            content_hash=record.content_hash,
            byte_size=record.byte_size,
            storage_uri=record.storage_uri,
        ),
        chunks=[
            DocumentChunk(
                id=chunk.id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                embedding=json.loads(chunk.embedding_json) if chunk.embedding_json else [],
            )
            for chunk in sorted(record.chunks, key=lambda item: item.chunk_index)
        ],
        entities=[
            entity_record_to_model(entity)
            for entity in sorted(record.entities, key=lambda item: (item.chunk_id, item.entity_type, item.text))
        ],
    )


async def application_level_search(
    repository: Repository,
    query_text: str,
    query_tokens: List[str],
    query_embedding: List[float],
    query_entity_keys: Set[Tuple[str, str]],
    limit: int,
) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
    """Backend-independent hybrid retrieval used by in-memory and SQLite stores.

    Produces the same unified [0, 1] score as the PostgreSQL-native path via the
    shared ``scoring`` module, so ranking scale and confidence are consistent
    across backends.
    """
    from collections import Counter

    corpus = await repository.iter_chunks()
    document_frequency = Counter()
    for _, chunk in corpus:
        document_frequency.update(set(query_tokens_from_text(chunk.content)))

    total_query_entities = len(query_entity_keys)
    query_lower = query_text.lower()
    # Filter conversational stopwords for lexical scoring, matching the
    # PostgreSQL-native path (which filters via plainto_tsquery/filter_search_tokens).
    # Without this, common words like "the"/"is"/"of" inflate BM25 and surface
    # irrelevant documents (and the two backends would rank differently).
    lexical_tokens = filter_search_tokens(query_tokens) or query_tokens

    raw_candidates: List[Tuple[float, float, float, float, DocumentResponse, DocumentChunk]] = []
    for document, chunk in corpus:
        chunk_tokens = query_tokens_from_text(chunk.content)
        if not chunk_tokens:
            continue
        lexical = bm25_lite(lexical_tokens, chunk_tokens, document_frequency, len(corpus))
        chunk_entities = await repository.entities_for_chunk(chunk.id)
        chunk_entity_keys = {(entity.entity_type, entity.normalized_text) for entity in chunk_entities}
        matched = len(query_entity_keys.intersection(chunk_entity_keys))
        entity_frac = entity_fraction(matched, total_query_entities)
        vector = cosine_similarity(query_embedding, chunk.embedding)
        phrase = 1.0 if query_lower and query_lower in chunk.content.lower() else 0.0
        raw_candidates.append((lexical, vector, entity_frac, phrase, document, chunk))

    normalized_lexical = normalize_lexical([item[0] for item in raw_candidates])
    scored: List[Tuple[float, DocumentResponse, DocumentChunk]] = []
    for (lexical, vector, entity_frac, phrase, document, chunk), lexical_norm in zip(raw_candidates, normalized_lexical):
        score = combine_signals(lexical_norm, vector, entity_frac, phrase)
        if score > 0:
            scored.append((score, document, chunk))
    return sorted(scored, key=lambda item: item[0], reverse=True)[:limit]


def query_tokens_from_text(text_value: str) -> List[str]:
    import re

    return [match.group(0).lower() for match in re.finditer(r"[A-Za-z0-9-]+", text_value)]


def build_or_tsquery(query_tokens: List[str]) -> str:
    """Build an OR-joined ``to_tsquery`` string from query tokens.

    The application (BM25) path rewards any matching term, so the PostgreSQL path
    must use OR semantics too — ``plainto_tsquery`` ANDs all terms, which made the
    two backends classify partial-match queries differently. Tokens are split to
    bare alphanumeric lexemes (Postgres' text search splits hyphenated tags like
    "P-101A" into "p"/"101a" as well) and de-duplicated. Injection-safe: only
    ``[a-z0-9]`` lexemes joined by " | ".
    """
    import re

    source = filter_search_tokens(query_tokens) or query_tokens
    lexemes: List[str] = []
    for token in source:
        for piece in re.sub(r"[^a-z0-9]+", " ", token.lower()).split():
            lexemes.append(piece)
    ordered_unique = list(dict.fromkeys(lexemes))
    return " | ".join(ordered_unique) or "zzznomatchzzz"


def filter_search_tokens(tokens: List[str]) -> List[str]:
    stopwords = {
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "how",
        "did",
        "does",
        "the",
        "to",
        "for",
        "and",
        "or",
        "of",
        "in",
        "on",
        "is",
        "was",
        "were",
        "happened",
        "happen",
    }
    return [token for token in tokens if token not in stopwords and len(token) > 1]


def entity_record_to_model(record: ExtractedEntityRecord) -> ExtractedEntity:
    return ExtractedEntity(
        id=record.id,
        document_id=record.document_id,
        chunk_id=record.chunk_id,
        entity_type=record.entity_type,
        text=record.text,
        normalized_text=record.normalized_text,
        confidence=record.confidence,
    )


async def ensure_corpus_state(session: AsyncSession) -> None:
    result = await session.execute(select(CorpusStateRecord).where(CorpusStateRecord.id == 1))
    if result.scalars().first() is None:
        session.add(CorpusStateRecord(id=1, version=0))
        await session.flush()


async def increment_cache_hit(session: AsyncSession, cache_id: int) -> None:
    await session.execute(
        text("UPDATE semantic_query_cache SET hit_count = hit_count + 1, updated_at = CURRENT_TIMESTAMP WHERE id = :cache_id"),
        {"cache_id": cache_id},
    )


def semantic_cache_record_to_model(record: SemanticCacheRecord) -> SemanticCacheEntry:
    return SemanticCacheEntry(
        normalized_query=record.normalized_query,
        query_embedding=json.loads(record.query_embedding_json),
        model_name=record.model_name,
        prompt_version=record.prompt_version,
        corpus_version=record.corpus_version,
        citation_hash=record.citation_hash,
        answer=record.answer,
        confidence=record.confidence,
        citations=[Citation(**citation) for citation in json.loads(record.citations_json)],
        retrieved_chunk_count=record.retrieved_chunk_count,
        query_type=record.query_type,
        cache_metadata=json.loads(record.cache_metadata_json or "{}"),
    )


def build_graph_for_documents(
    documents: List[DocumentResponse],
    required_entity: Optional[Tuple[str, str]] = None,
) -> KnowledgeGraph:
    nodes: Dict[str, GraphNode] = {}
    edges: Dict[str, GraphEdge] = {}

    for document in documents:
        document_node_id = f"document:{document.id}"
        nodes[document_node_id] = GraphNode(
            id=document_node_id,
            label=document.metadata.filename,
            type="DOCUMENT",
            metadata={"document_type": document.metadata.document_type, "status": document.status},
        )

        unique_entities = dedupe_entities(document.entities)
        for entity in unique_entities:
            entity_node_id = entity_node_key(entity.entity_type, entity.normalized_text)
            nodes.setdefault(
                entity_node_id,
                GraphNode(
                    id=entity_node_id,
                    label=entity.text,
                    type=entity.entity_type,
                    metadata={"normalized_text": entity.normalized_text},
                ),
            )
            edge_id = f"{document_node_id}->references->{entity_node_id}"
            existing = edges.get(edge_id)
            edges[edge_id] = GraphEdge(
                id=edge_id,
                source=document_node_id,
                target=entity_node_id,
                type="references",
                weight=max(existing.weight if existing else 0.0, entity.confidence),
                metadata={"document_id": document.id},
            )

        for left, right in combinations(sorted(unique_entities, key=lambda item: item.id), 2):
            if left.normalized_text == right.normalized_text and left.entity_type == right.entity_type:
                continue
            left_key = entity_node_key(left.entity_type, left.normalized_text)
            right_key = entity_node_key(right.entity_type, right.normalized_text)
            edge_id = f"{left_key}->co_occurs->{right_key}:{document.id}"
            edges[edge_id] = GraphEdge(
                id=edge_id,
                source=left_key,
                target=right_key,
                type="co_occurs",
                weight=0.65,
                metadata={"document_id": document.id},
            )

    if required_entity:
        required_key = entity_node_key(required_entity[0], required_entity[1])
        connected = connected_node_ids(required_key, edges.values())
        nodes = {node_id: node for node_id, node in nodes.items() if node_id in connected}
        edges = {
            edge_id: edge
            for edge_id, edge in edges.items()
            if edge.source in nodes and edge.target in nodes
        }

    return KnowledgeGraph(nodes=list(nodes.values()), edges=list(edges.values()))


def dedupe_entities(entities: List[ExtractedEntity]) -> List[ExtractedEntity]:
    by_key: Dict[Tuple[str, str], ExtractedEntity] = {}
    for entity in entities:
        key = (entity.entity_type, entity.normalized_text)
        existing = by_key.get(key)
        if not existing or entity.confidence > existing.confidence:
            by_key[key] = entity
    return list(by_key.values())


def entity_node_key(entity_type: str, normalized_text: str) -> str:
    return f"{entity_type.lower()}:{normalized_text}"


def connected_node_ids(seed_id: str, edges: Iterable[GraphEdge]) -> Set[str]:
    connected = {seed_id}
    changed = True
    edge_list = list(edges)
    while changed:
        changed = False
        for edge in edge_list:
            if edge.source in connected and edge.target not in connected:
                connected.add(edge.target)
                changed = True
            if edge.target in connected and edge.source not in connected:
                connected.add(edge.source)
                changed = True
    return connected


def normalize_graph_value(entity_type: str, raw_value: str) -> str:
    value = raw_value.upper().strip()
    if entity_type == "EQUIPMENT_TAG":
        # Resolve spelled-out equipment (e.g. "Pump 101-A") the same way the
        # extractor does, so subgraph lookups match aliased tags consistently.
        alias_match = EQUIPMENT_NOUN_PATTERN.search(raw_value.strip())
        if alias_match:
            canonical = canonical_equipment_tag(alias_match.group(1), alias_match.group(2), alias_match.group(3))
            if canonical:
                return normalize_entity("EQUIPMENT_TAG", canonical)
        return "".join(char for char in value if char.isalnum())
    if entity_type in {"PROCEDURE_ID", "PART_NUMBER", "REGULATORY_REF"}:
        return value.replace(" ", "")
    return " ".join(value.replace("_", " ").split())
