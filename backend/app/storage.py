import os
from pathlib import Path
from typing import Protocol


class StorageBackend(Protocol):
    async def store(self, document_id: str, filename: str, payload: bytes) -> str: ...

    async def read(self, storage_uri: str) -> bytes: ...


class LocalStorage:
    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def store(self, document_id: str, filename: str, payload: bytes) -> str:
        safe_name = sanitize_filename(filename)
        document_dir = self.base_path / document_id
        document_dir.mkdir(parents=True, exist_ok=True)
        path = document_dir / safe_name
        path.write_bytes(payload)
        return f"local://{path.resolve()}"

    async def read(self, storage_uri: str) -> bytes:
        if not storage_uri.startswith("local://"):
            raise ValueError("Unsupported local storage URI")
        path = Path(storage_uri.removeprefix("local://"))
        if not path.exists() or not path.is_file():
            raise FileNotFoundError("Stored document not found")
        return path.read_bytes()


class S3Storage:
    def __init__(self, bucket: str, prefix: str = "") -> None:
        if not bucket:
            raise ValueError("S3 bucket is required")
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("S3 storage requires boto3") from exc
        self.client = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    async def store(self, document_id: str, filename: str, payload: bytes) -> str:
        safe_name = sanitize_filename(filename)
        key = "/".join(part for part in [self.prefix, document_id, safe_name] if part)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload)
        return f"s3://{self.bucket}/{key}"

    async def read(self, storage_uri: str) -> bytes:
        if not storage_uri.startswith("s3://"):
            raise ValueError("Unsupported S3 storage URI")
        bucket_and_key = storage_uri.removeprefix("s3://")
        bucket, key = bucket_and_key.split("/", 1)
        response = self.client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()


def create_storage_backend(
    backend: str,
    local_path: str,
    s3_bucket: str,
    s3_prefix: str,
) -> StorageBackend:
    if backend == "local":
        return LocalStorage(local_path)
    if backend == "s3":
        return S3Storage(bucket=s3_bucket, prefix=s3_prefix)
    raise ValueError(f"Unsupported storage backend: {backend}")


def sanitize_filename(filename: str) -> str:
    base = os.path.basename(filename or "uploaded-document")
    cleaned = "".join(char if char.isalnum() or char in {".", "-", "_"} else "_" for char in base)
    return cleaned or "uploaded-document"
