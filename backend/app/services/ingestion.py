import hashlib
from uuid import uuid4

from ..config import Settings
from ..errors import UploadTooLargeError
from ..models import DocumentMetadata, DocumentResponse
from ..repository import Repository
from ..storage import StorageBackend
from .chunking import SemanticChunker
from .embeddings import EmbeddingProvider
from .entities import IndustrialEntityExtractor
from .parsers import DocumentParser


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
            ),
            chunks=chunks,
            entities=entities,
        )
        return await self.repository.add_document(document)
