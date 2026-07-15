from contextlib import asynccontextmanager
import logging
from secrets import compare_digest
import time
from typing import AsyncIterator, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .database import create_engine_and_session_factory, initialize_database
from .errors import UploadTooLargeError
from .models import (
    AuditEvent,
    CopilotQuery,
    CopilotResponse,
    DocumentResponse,
    EquipmentHealthReport,
    FailureClusterReport,
    KnowledgeGraph,
    MaintenancePrediction,
    RCAReport,
)
from .repository import InMemoryRepository, Repository, SqlAlchemyRepository
from .services.copilot import CopilotService
from .services.embeddings import create_embedding_provider
from .services.exact_cache import create_exact_cache
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

    return app


app = create_app()
