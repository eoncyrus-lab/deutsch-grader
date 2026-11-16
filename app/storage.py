from __future__ import annotations

import base64
import hashlib
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.settings import settings


class StorageError(RuntimeError):
    pass


class ObjectStore:
    """Tenant-prefixed, encrypted-at-rest object storage for handwritten images."""

    def __init__(self, root: Path | None = None, secret: str | None = None):
        self.root = Path(root or settings.storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        material = (secret or settings.storage_key or settings.secret_key).encode("utf-8")
        key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        self._fernet = Fernet(key)

    def _path(self, tenant_id: str, object_key: str) -> Path:
        if not object_key.startswith(f"{tenant_id}/"):
            raise StorageError("object key does not belong to tenant")
        path = (self.root / object_key).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError("object key escapes storage root")
        return path

    def put(self, tenant_id: str, data: bytes, content_type: str) -> dict[str, object]:
        object_key = f"{tenant_id}/{uuid.uuid4().hex}.enc"
        path = self._path(tenant_id, object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._fernet.encrypt(data))
        os.chmod(path, 0o600)
        return {
            "object_key": object_key,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "content_type": content_type,
        }

    def get(self, tenant_id: str, object_key: str) -> bytes:
        path = self._path(tenant_id, object_key)
        try:
            return self._fernet.decrypt(path.read_bytes())
        except InvalidToken as exc:
            raise StorageError("cannot decrypt object") from exc

    def delete(self, tenant_id: str, object_key: str) -> None:
        try:
            self._path(tenant_id, object_key).unlink(missing_ok=True)
        except StorageError:
            return
