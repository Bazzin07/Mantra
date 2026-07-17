from contextlib import asynccontextmanager
import json
import logging
from secrets import compare_digest
import time
from typing import AsyncIterator, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings, get_settings
from .database import create_engine_and_session_factory, initialize_database
from .errors import UploadTooLargeError
from .models import (
    AdminOverview,
    AuditEvent,
    ComplianceGap,
    ComplianceStatus,
    CopilotQuery,
    CopilotResponse,
    DocumentResponse,
    DocumentSummary,
    EquipmentCompliance,
    EquipmentHealthReport,
    EvidencePackage,
    FailureClusterReport,
    FailurePattern,
    FailureWarning,
    IncidentAnalysis,
    IngestionFailure,
    KnowledgeGraph,
    MaintenancePrediction,
    PatternReport,
    RCAReport,
    SimilarIncidentReport,
)
from .repository import InMemoryRepository, Repository, SqlAlchemyRepository
from .services.admin import AdminService
from .services.compliance import ComplianceService
from .services.copilot import CopilotService
from .services.embeddings import create_embedding_provider
from .services.exact_cache import create_exact_cache
from .services.failure_intelligence import FailureIntelligenceService
from .services.ingestion import IngestionService
from .services.model_providers import create_model_providers
from .services.rca import RCAService
from .services.parsers import DependencyUnavailableError, DocumentParseError, UnsupportedFormatError
from .storage import create_storage_backend, sanitize_filename


logger = logging.getLogger(__name__)


def create_app(settings: Optional[Settings] = None, repository: Optional[Repository] = None) -> FastAPI:
    settings = settings or get_settings()

    # Fail closed: production must not boot with the data API unauthenticated.
    if settings.environment == "production" and not (settings.require_api_key and settings.api_key):
        raise RuntimeError("Production requires IKI_REQUIRE_API_KEY=true and IKI_API_KEY set")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_repository = repository
        storage = create_storage_backend(
            settings.storage_backend,
            settings.local_storage_path,
            settings.s3_bucket,
            settings.s3_prefix,
        )
        embedding_provider = create_embedding_provider(
            settings.embedding_backend,
            settings.embedding_model,
            settings.embedding_dimension,
        )
        engine = None
        if app_repository is None:
            if settings.repository_backend == "memory":
                app_repository = InMemoryRepository()
            else:
                engine, session_factory = create_engine_and_session_factory(settings.database_url)
                if settings.auto_create_schema:
                    await initialize_database(engine)
                app_repository = SqlAlchemyRepository(session_factory)

        app.state.repository = app_repository
        app.state.storage = storage
        app.state.embedding_provider = embedding_provider
        app.state.ingestion = IngestionService(app_repository, settings, storage, embedding_provider)
        llm_provider, reranker_provider, safety_provider = create_model_providers(settings)
        app.state.copilot = CopilotService(
            app_repository,
            embedding_provider,
            settings=settings,
            llm_provider=llm_provider,
            reranker_provider=reranker_provider,
            safety_provider=safety_provider,
            exact_cache=create_exact_cache(settings.redis_url),
        )
        app.state.rca = RCAService(
            app_repository, embedding_provider, settings=settings, llm_provider=llm_provider
        )
        app.state.compliance = ComplianceService(
            app_repository, embedding_provider, settings=settings, llm_provider=llm_provider
        )
        app.state.failure_intelligence = FailureIntelligenceService(
            app_repository, embedding_provider, app.state.rca, settings=settings, llm_provider=llm_provider
        )
        app.state.admin = AdminService(app_repository)
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Production-oriented backend for document ingestion, knowledge graph construction, and cited copilot retrieval.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def require_api_key(x_api_key: Optional[str] = Header(default=None, alias=settings.api_key_header)) -> None:
        if not settings.require_api_key:
            return
        if not settings.api_key:
            raise HTTPException(status_code=500, detail="API key authentication is enabled but no API key is configured")
        if x_api_key is None or not compare_digest(x_api_key, settings.api_key):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # ponytail: in-memory fixed-window limiter keyed by client host — correct for
    # the current single-API-instance topology. Move counts to Redis if you run
    # >1 replica; swap for a sliding window if the once-per-window boundary burst
    # (briefly up to ~2x) matters. Registered before audit so 429s are still
    # logged. rate_limit_per_minute=0 (default) disables it for dev/tests.
    rate_hits: dict[str, tuple[int, int]] = {}

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        limit = settings.rate_limit_per_minute
        if limit > 0 and request.url.path != "/api/health":
            host = request.client.host if request.client else "unknown"
            window = int(time.time() // 60)
            stored_window, count = rate_hits.get(host, (window, 0))
            count = count + 1 if stored_window == window else 1
            rate_hits[host] = (window, count)
            if count > limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Try again shortly."},
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)

    @app.middleware("http")
    async def audit_requests(request: Request, call_next):
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            repository_state = getattr(request.app.state, "repository", None)
            if repository_state is not None:
                try:
                    await repository_state.add_audit_event(
                        AuditEvent(
                            method=request.method,
                            path=request.url.path,
                            status_code=status_code,
                            duration_ms=duration_ms,
                            client_host=request.client.host if request.client else "",
                            user_agent=request.headers.get("user-agent", ""),
                        )
                    )
                except Exception:
                    logger.warning(
                        "request audit logging failed",
                        exc_info=True,
                        extra={"method": request.method, "path": request.url.path, "status_code": status_code},
                    )

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "environment": settings.environment,
            "repository_backend": settings.repository_backend,
            "documents": await app.state.repository.count_documents(),
        }

    @app.post("/api/documents/upload", response_model=DocumentResponse, dependencies=[Depends(require_api_key)])
    async def upload_document(file: UploadFile = File(...)) -> DocumentResponse:
        try:
            payload = await file.read()
            return await app.state.ingestion.ingest(
                filename=file.filename or "uploaded-document",
                content_type=file.content_type or "application/octet-stream",
                payload=payload,
            )
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))
        except UnsupportedFormatError as exc:
            raise HTTPException(status_code=415, detail=str(exc))
        except DependencyUnavailableError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except DocumentParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/documents", response_model=list[DocumentSummary], dependencies=[Depends(require_api_key)])
    async def list_documents(document_type: Optional[str] = None, equipment_tag: Optional[str] = None) -> list[DocumentSummary]:
        return await app.state.admin.list_documents(document_type=document_type, equipment_tag=equipment_tag)

    @app.get("/api/documents/{document_id}", response_model=DocumentResponse, dependencies=[Depends(require_api_key)])
    async def get_document(document_id: str) -> DocumentResponse:
        document = await app.state.repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return document

    @app.get("/api/documents/{document_id}/raw", dependencies=[Depends(require_api_key)])
    async def get_raw_document(document_id: str) -> Response:
        document = await app.state.repository.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        try:
            payload = await app.state.storage.read(document.metadata.storage_uri)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Stored document not found")
        return Response(
            content=payload,
            media_type=document.metadata.content_type,
            headers={"Content-Disposition": f'attachment; filename="{sanitize_filename(document.metadata.filename)}"'},
        )

    @app.get("/api/knowledge-graph", response_model=KnowledgeGraph, dependencies=[Depends(require_api_key)])
    async def knowledge_graph() -> KnowledgeGraph:
        return await app.state.repository.build_graph()

    @app.get("/api/knowledge-graph/equipment/{equipment_tag}", response_model=KnowledgeGraph, dependencies=[Depends(require_api_key)])
    async def equipment_graph(equipment_tag: str) -> KnowledgeGraph:
        graph = await app.state.repository.build_graph_for_entity("EQUIPMENT_TAG", equipment_tag)
        if not graph.nodes:
            raise HTTPException(status_code=404, detail="Equipment tag not found")
        return graph

    @app.post("/api/copilot/ask", response_model=CopilotResponse, dependencies=[Depends(require_api_key)])
    async def ask_copilot(query: CopilotQuery) -> CopilotResponse:
        return await app.state.copilot.answer(query.question, limit=query.limit)

    @app.post("/api/copilot/stream", dependencies=[Depends(require_api_key)])
    async def stream_copilot(query: CopilotQuery) -> StreamingResponse:
        async def event_source() -> AsyncIterator[str]:
            async for chunk in app.state.copilot.stream(query.question, limit=query.limit):
                yield f"data: {json.dumps(chunk)}\n\n"

        return StreamingResponse(event_source(), media_type="text/event-stream")

    @app.post("/api/maintenance/rca/{equipment_id}", response_model=RCAReport, dependencies=[Depends(require_api_key)])
    async def rca_for_equipment(equipment_id: str) -> RCAReport:
        return await app.state.rca.rca("EQUIPMENT_TAG", equipment_id)

    @app.get("/api/maintenance/health/{equipment_id}", response_model=EquipmentHealthReport, dependencies=[Depends(require_api_key)])
    async def equipment_health(equipment_id: str) -> EquipmentHealthReport:
        return await app.state.rca.health(equipment_id)

    @app.get("/api/maintenance/predictions", response_model=list[MaintenancePrediction], dependencies=[Depends(require_api_key)])
    async def maintenance_predictions() -> list[MaintenancePrediction]:
        return await app.state.rca.predictions()

    @app.get("/api/maintenance/clusters", response_model=FailureClusterReport, dependencies=[Depends(require_api_key)])
    async def failure_clusters() -> FailureClusterReport:
        return await app.state.rca.cluster_failures()

    @app.post("/api/maintenance/investigate", response_model=RCAReport, dependencies=[Depends(require_api_key)])
    async def investigate(query: CopilotQuery) -> RCAReport:
        return await app.state.rca.investigate(query.question)

    @app.get("/api/compliance/status", response_model=ComplianceStatus, dependencies=[Depends(require_api_key)])
    async def compliance_status() -> ComplianceStatus:
        return await app.state.compliance.status()

    @app.get("/api/compliance/gaps", response_model=list[ComplianceGap], dependencies=[Depends(require_api_key)])
    async def compliance_gaps() -> list[ComplianceGap]:
        return await app.state.compliance.gaps()

    @app.post("/api/compliance/audit/{regulation}", response_model=EvidencePackage, dependencies=[Depends(require_api_key)])
    async def compliance_audit(regulation: str) -> EvidencePackage:
        return await app.state.compliance.audit(regulation)

    @app.get("/api/compliance/equipment/{equipment_id}", response_model=EquipmentCompliance, dependencies=[Depends(require_api_key)])
    async def compliance_equipment(equipment_id: str) -> EquipmentCompliance:
        return await app.state.compliance.equipment_status(equipment_id)

    @app.get("/api/failures/patterns", response_model=PatternReport, dependencies=[Depends(require_api_key)])
    async def failure_patterns() -> PatternReport:
        return await app.state.failure_intelligence.patterns()

    @app.get("/api/failures/analysis/{document_id}", response_model=IncidentAnalysis, dependencies=[Depends(require_api_key)])
    async def failure_analysis(document_id: str) -> IncidentAnalysis:
        analysis = await app.state.failure_intelligence.analyze_incident(document_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return analysis

    @app.get("/api/failures/similar/{document_id}", response_model=SimilarIncidentReport, dependencies=[Depends(require_api_key)])
    async def failure_similar(document_id: str) -> SimilarIncidentReport:
        report = await app.state.failure_intelligence.similar_incidents(document_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return report

    @app.post("/api/failures/analyze", response_model=FailureWarning, dependencies=[Depends(require_api_key)])
    async def failure_analyze(document_id: str) -> FailureWarning:
        warning = await app.state.failure_intelligence.check_new_document(document_id)
        if warning is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return warning

    @app.get("/api/lessons", response_model=list[FailurePattern], dependencies=[Depends(require_api_key)])
    async def lessons(q: Optional[str] = None) -> list[FailurePattern]:
        report = await app.state.failure_intelligence.patterns()
        if not q:
            return report.patterns
        needle = q.lower()
        return [
            p for p in report.patterns
            if needle in p.description.lower()
            or any(needle in eq.lower() for eq in p.affected_equipment)
            or any(needle in f.lower() for f in p.document_filenames)
        ]

    @app.get("/api/admin/overview", response_model=AdminOverview, dependencies=[Depends(require_api_key)])
    async def admin_overview() -> AdminOverview:
        return await app.state.admin.overview()

    @app.get(
        "/api/admin/ingestion-failures",
        response_model=list[IngestionFailure],
        dependencies=[Depends(require_api_key)],
    )
    async def list_ingestion_failures() -> list[IngestionFailure]:
        return await app.state.repository.list_ingestion_failures()

    @app.post(
        "/api/admin/ingestion-failures/{failure_id}/reprocess",
        response_model=DocumentResponse,
        dependencies=[Depends(require_api_key)],
    )
    async def reprocess_ingestion_failure(failure_id: str) -> DocumentResponse:
        try:
            return await app.state.ingestion.reprocess_failure(failure_id)
        except UnsupportedFormatError as exc:
            raise HTTPException(status_code=415, detail=str(exc))
        except DependencyUnavailableError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except DocumentParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except ValueError as exc:
            status_code = 404 if "No ingestion failure found" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc))

    @app.delete(
        "/api/admin/ingestion-failures/{failure_id}",
        status_code=204,
        dependencies=[Depends(require_api_key)],
    )
    async def dismiss_ingestion_failure(failure_id: str) -> Response:
        await app.state.repository.delete_ingestion_failure(failure_id)
        return Response(status_code=204)

    return app


app = create_app()
