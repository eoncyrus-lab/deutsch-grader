import pytest

from app.storage import ObjectStore, StorageError


def test_roundtrip_and_tenant_prefix(tmp_path):
    store = ObjectStore(root=tmp_path, secret="test-secret")
    stored = store.put("tenant-a", b"hello", "text/plain")
    assert store.get("tenant-a", stored["object_key"]) == b"hello"
    with pytest.raises(StorageError):
        store.get("tenant-b", stored["object_key"])
    store.delete("tenant-a", stored["object_key"])
    with pytest.raises(FileNotFoundError):
        store.get("tenant-a", stored["object_key"])
