# Mantra Industrial Knowledge Intelligence Backend

Mantra is a retrieval-first backend for industrial document intelligence. It focuses on two initial capabilities:

1. Universal Document Ingestion & Knowledge Graph Agent
2. Expert Knowledge Copilot

The backend ingests heterogeneous plant documents, extracts operational entities, builds a lightweight industrial knowledge graph, and answers user questions with cited evidence.

## Architecture Overview

- **API:** FastAPI async backend.
- **Persistence:** SQLAlchemy with SQLite for local development and PostgreSQL for production.
- **Search:** PostgreSQL `pgvector` semantic retrieval plus `tsvector` keyword search.
- **Storage:** local raw-document storage with an S3-compatible abstraction.
- **Parsing:** adapters for text, PDF, DOCX, XLSX, PPTX, EML, and image OCR.
- **Entity extraction:** deterministic industrial extraction for equipment tags, procedures, regulations, dates, people, parts, and failure terms.
- **Graph:** document/entity nodes with reference and co-occurrence edges.
- **Copilot orchestration:** LangGraph workflow for cache lookup, retrieval, reranking, answer generation, safety handling, and cache writeback.
- **Model layer:** deterministic offline fallback plus NVIDIA-compatible provider abstractions for reranking and answer generation.
- **Caching:** PostgreSQL semantic cache with optional Redis exact-cache layer.
- **Security baseline:** optional API-key protection, request audit logging, and secret loading from local runtime env files only.

## Runtime Components

The backend expects:

- Python 3.11+
- PostgreSQL with `pgvector` for production mode
- Redis for optional exact-cache acceleration
- Tesseract and Poppler for OCR/PDF support
- NVIDIA API key only when `IKI_LLM_PROVIDER=nvidia`

## Configuration

Do not commit secrets. For local secrets, create:

```text
backend/.env
```

Minimum NVIDIA-backed copilot settings:

```env
IKI_LLM_PROVIDER=nvidia
NVIDIA_API_KEY=replace-with-local-key
IKI_NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
```

Production-quality sentence-transformer embeddings can be enabled with:

```env
IKI_EMBEDDING_BACKEND=sentence-transformers
IKI_EMBEDDING_MODEL=BAAI/bge-base-en-v1.5
IKI_EMBEDDING_DIMENSION=768
```

Documents indexed with hashing embeddings should be reindexed before relying on BGE semantic search.

## Local Backend Run

```bash
python3 -m pip install -r requirements.txt
python3 -m alembic upgrade head
uvicorn backend.app.main:app --reload
```

## Docker Run

```bash
docker compose up -d api
```

The Compose stack starts PostgreSQL, Redis, and the API. Runtime secrets are loaded from `backend/.env` and are not copied into the image.
