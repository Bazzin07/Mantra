import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from ..config import Settings
from ..errors import UploadTooLargeError
from ..models import DocumentMetadata, DocumentResponse, IngestionFailure
from ..repository import Repository
from ..storage import StorageBackend
from .chunking import SemanticChunker
from .embeddings import EmbeddingProvider
from .entities import IndustrialEntityExtractor
from .parsers import DependencyUnavailableError, DocumentParseError, DocumentParser, UnsupportedFormatError

# Errors worth recording for later manual reprocessing: a bad parser dependency
# or a parse failure may be fixed by an operator (install a lib, the file
# itself may just be a bad format) without the original uploader re-sending
# the file. UploadTooLargeError and the empty-payload check are NOT retryable
# without a different file, so they're deliberately excluded from the queue.
RETRYABLE_INGESTION_ERRORS = (UnsupportedFormatError, DependencyUnavailableError, DocumentParseError, ValueError)


class IngestionService:
    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        storage: StorageBackend,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.repository = repository
        self.parser = DocumentParser()
        self.chunker = SemanticChunker(chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
        self.entity_extractor = IndustrialEntityExtractor()
        self.settings = settings
        self.storage = storage
        self.embedding_provider = embedding_provider

    async def ingest(self, filename: str, content_type: str, payload: bytes) -> DocumentResponse:
        if not payload:
            raise ValueError("Uploaded document is empty")
        if len(payload) > self.settings.max_upload_bytes:
            raise UploadTooLargeError(f"Uploaded document exceeds {self.settings.max_upload_bytes} bytes")

        content_hash = hashlib.sha256(payload).hexdigest()
        duplicate = await self.repository.find_duplicate(content_hash)
        if duplicate:
            return duplicate.model_copy(update={"status": "duplicate", "duplicate_of": duplicate.id})

        try:
            return await self._parse_and_index(filename, content_type, payload, content_hash)
        except RETRYABLE_INGESTION_ERRORS as exc:
            await self._record_new_failure(filename, content_type, payload, str(exc))
            raise

    async def reprocess_failure(self, failure_id: str) -> DocumentResponse:
        """Re-runs ingestion for a previously failed upload using its stored
        payload (FR-36-adjacent: manual, operator-triggered — no automatic
        retry loop, no scheduler). On success the failure record is deleted;
        on repeat failure it's kept with an updated attempt count/message."""
        failure = await self.repository.get_ingestion_failure(failure_id)
        if failure is None:
            raise ValueError(f"No ingestion failure found for id {failure_id}")
        payload = await self.storage.read(failure.storage_uri)
        content_hash = hashlib.sha256(payload).hexdigest()
        duplicate = await self.repository.find_duplicate(content_hash)
        if duplicate:
            await self.repository.delete_ingestion_failure(failure_id)
            return duplicate.model_copy(update={"status": "duplicate", "duplicate_of": duplicate.id})
        try:
            document = await self._parse_and_index(failure.filename, failure.content_type, payload, content_hash)
        except RETRYABLE_INGESTION_ERRORS as exc:
            await self.repository.touch_ingestion_failure(failure_id, str(exc))
            raise
        await self.repository.delete_ingestion_failure(failure_id)
        return document

    async def _parse_and_index(
        self, filename: str, content_type: str, payload: bytes, content_hash: str
    ) -> DocumentResponse:
        parsed = self.parser.parse(filename, content_type, payload)
        document_id = str(uuid4())
        storage_uri = await self.storage.store(document_id, filename, payload)
        chunks = self.chunker.chunk_pages(document_id, parsed.pages)
        if not chunks:
            raise ValueError("No extractable text found in document")
        chunks = [
            chunk.model_copy(update={"embedding": self.embedding_provider.embed(chunk.content)})
            for chunk in chunks
        ]

        entities = self.entity_extractor.extract_from_chunks(chunks, document_id=document_id)
        document = DocumentResponse(
            id=document_id,
            status="indexed",
            metadata=DocumentMetadata(
                filename=filename,
                content_type=content_type,
                document_type=parsed.document_type,
                page_count=len(parsed.pages),
                content_hash=content_hash,
                byte_size=len(payload),
                storage_uri=storage_uri,
                created_at=datetime.now(timezone.utc).isoformat(),
            ),
            chunks=chunks,
            entities=entities,
        )
        return await self.repository.add_document(document)

    async def _record_new_failure(self, filename: str, content_type: str, payload: bytes, error_message: str) -> None:
        failure_id = str(uuid4())
        storage_uri = await self.storage.store(failure_id, filename, payload)
        await self.repository.add_ingestion_failure(
            IngestionFailure(
                id=failure_id,
                filename=filename,
                content_type=content_type,
                byte_size=len(payload),
                storage_uri=storage_uri,
                error_message=error_message,
            )
        )
