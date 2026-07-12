from typing import List
from uuid import uuid4

from ..models import DocumentChunk


class SemanticChunker:
    def __init__(self, chunk_size: int = 500, overlap: int = 100) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_pages(self, document_id: str, pages: List[str]) -> List[DocumentChunk]:
        chunks: List[DocumentChunk] = []
        for page_index, page_text in enumerate(pages, start=1):
            normalized = normalize_whitespace(page_text)
            if not normalized:
                continue
            start = 0
            while start < len(normalized):
                end = min(start + self.chunk_size, len(normalized))
                chunk_text = normalized[start:end].strip()
                if chunk_text:
                    chunks.append(
                        DocumentChunk(
                            id=str(uuid4()),
                            document_id=document_id,
                            chunk_index=len(chunks),
                            content=chunk_text,
                            page_number=page_index,
                        )
                    )
                if end == len(normalized):
                    break
                start = max(end - self.overlap, start + 1)
        return chunks


def normalize_whitespace(text: str) -> str:
    return " ".join(text.replace("\x00", " ").split())
