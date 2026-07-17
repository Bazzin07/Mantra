"""Module 7/8: document statistics, usage, and pipeline visibility (FR-30,
FR-32, FR-34, FR-35). Every number here is derived from real, already-stored
data — document records and the audit_events table populated by main.py's
existing audit middleware. Nothing here is estimated or invented: where a
requested metric has no honest source (cache-hit rate, per-call $ cost,
async pipeline/retry state — this system has none of those), the field is
omitted and the reason is stated in `note`, not guessed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from ..models import (
    AdminOverview,
    DocumentStats,
    DocumentSummary,
    DocumentTypeCount,
    EndpointUsage,
    PipelineStatus,
    UsageStats,
)
from ..repository import Repository

# Endpoints that can invoke the LLM provider (directly, or via a service that
# does). A request here may still resolve from cache or fall back to the
# deterministic template — this counts *requests to LLM-capable endpoints*,
# not confirmed model calls, and is labeled as such in UsageStats.note.
LLM_INVOKING_PATH_PREFIXES = (
    "/api/copilot/ask",
    "/api/copilot/stream",
    "/api/maintenance/rca/",
    "/api/maintenance/investigate",
    "/api/compliance/audit/",
    "/api/failures/analyze",
    "/api/failures/analysis/",
)


class AdminService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    async def overview(self) -> AdminOverview:
        documents = await self.repository.list_documents()
        events = await self.repository.recent_audit_events(limit=5000)

        by_type: Dict[str, int] = defaultdict(int)
        by_status: Dict[str, int] = defaultdict(int)
        dates: List[str] = []
        for document in documents:
            by_type[document.metadata.document_type] += 1
            by_status[document.status] += 1
            if document.metadata.created_at:
                dates.append(document.metadata.created_at)

        document_stats = DocumentStats(
            total_documents=len(documents),
            by_type=[DocumentTypeCount(document_type=k, count=v) for k, v in sorted(by_type.items())],
            by_status=dict(by_status),
            earliest_ingested=min(dates) if dates else None,
            latest_ingested=max(dates) if dates else None,
        )

        by_path: Dict[str, list] = defaultdict(list)
        for event in events:
            by_path[event.path].append(event)

        by_endpoint: List[EndpointUsage] = []
        llm_invoking_requests = 0
        total_errors = 0
        for path, path_events in sorted(by_path.items()):
            count = len(path_events)
            errors = sum(1 for e in path_events if e.status_code >= 400)
            total_errors += errors
            avg_duration = sum(e.duration_ms for e in path_events) / count if count else 0.0
            by_endpoint.append(
                EndpointUsage(path=path, request_count=count, avg_duration_ms=round(avg_duration, 1), error_count=errors)
            )
            if path.startswith(LLM_INVOKING_PATH_PREFIXES):
                llm_invoking_requests += count

        usage_stats = UsageStats(
            total_requests=len(events),
            total_errors=total_errors,
            llm_invoking_requests=llm_invoking_requests,
            by_endpoint=by_endpoint,
            note=(
                "Request counts and average latency are real, from the audit log (in-memory backend "
                "keeps no audit history, so this is empty there). Cache-hit rate and per-call cost are "
                "not tracked (cache_status isn't persisted per request, and the shared NVIDIA endpoint "
                "exposes no token-level pricing) — omitted rather than estimated."
            ),
        )

        upload_events = by_path.get("/api/documents/upload", [])
        upload_errors = sum(1 for e in upload_events if e.status_code >= 400)
        pending_count = len(await self.repository.list_ingestion_failures(limit=10_000))
        pipeline_status = PipelineStatus(
            documents_indexed=by_status.get("indexed", 0),
            documents_duplicate=by_status.get("duplicate", 0),
            upload_errors_recent=upload_errors,
            pending_reprocessing=pending_count,
            note=(
                "Ingestion is synchronous (parse/chunk/extract/embed/store within one request) — there is "
                "no async pipeline queue or scheduler. Uploads that fail parsing/extraction (not oversized "
                "or empty payloads, which need a different file) are recorded with their original bytes and "
                "can be manually reprocessed via /api/admin/ingestion-failures; nothing retries automatically."
            ),
        )

        return AdminOverview(documents=document_stats, usage=usage_stats, pipeline=pipeline_status)

    async def list_documents(
        self, document_type: Optional[str] = None, equipment_tag: Optional[str] = None
    ) -> List[DocumentSummary]:
        if equipment_tag:
            documents = await self.repository.documents_for_entity("EQUIPMENT_TAG", equipment_tag)
        else:
            documents = await self.repository.list_documents()
        if document_type:
            documents = [d for d in documents if d.metadata.document_type == document_type]
        ranked = sorted(documents, key=lambda d: d.metadata.created_at, reverse=True)
        return [
            DocumentSummary(
                id=d.id, filename=d.metadata.filename, document_type=d.metadata.document_type,
                status=d.status, byte_size=d.metadata.byte_size, created_at=d.metadata.created_at,
            )
            for d in ranked
        ]
