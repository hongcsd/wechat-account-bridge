import hashlib
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status


class StoredUpload:
    def __init__(self, path: Path, filename: str, content_type: str, size: int, sha256: str):
        self.path = path
        self.filename = filename
        self.content_type = content_type
        self.size = size
        self.sha256 = sha256


async def store_upload(upload: UploadFile, temp_dir: Path, max_bytes: int) -> StoredUpload:
    temp_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "upload.bin").suffix
    safe_name = f"{int(time.time())}-{uuid4().hex}{suffix}"
    path = temp_dir / safe_name

    digest = hashlib.sha256()
    size = 0
    with path.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds {max_bytes} bytes",
                )
            digest.update(chunk)
            out.write(chunk)

    return StoredUpload(
        path=path,
        filename=upload.filename or safe_name,
        content_type=upload.content_type or "application/octet-stream",
        size=size,
        sha256=digest.hexdigest(),
    )


def cleanup_expired(temp_dir: Path, ttl_seconds: int) -> int:
    if not temp_dir.exists():
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for path in temp_dir.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed
