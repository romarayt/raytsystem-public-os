"""Envelope encryption: honest availability, approvals, rotation, tamper detection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import subprocess
import sys
import types
from pathlib import Path

import pytest

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts import ApprovalRecord, EncryptedBlob, canonical_json_bytes, sha256_hex
from raytsystem.contracts.lifecycle import KeyProviderState
from raytsystem.secrets import (
    EnvironmentKeyProvider,
    ExternalKmsKeyProvider,
    MacOSKeychainProvider,
    SecretEncryptionError,
    SecretEncryptionService,
    UnavailableKeyProvider,
)

pytestmark = pytest.mark.filterwarnings("error")

KEY_VARIABLE = "RAYTSYSTEM_TEST_SECRET_KEY"
KEY_ID = "key_environment"
ASSOCIATED = b"workspace:test-context"
PLAINTEXT = b"SUPER-SECRET-VALUE"
_AEAD_MODULE = "cryptography.hazmat.primitives.ciphers.aead"


class _StdlibAesGcmDouble:
    """Deterministic AEAD test double with the AESGCM interface (tag-verified)."""

    def __init__(self, key: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError("AESGCM key must be 128, 192, or 256 bits")
        self._key = key

    def _stream(self, nonce: bytes, length: int) -> bytes:
        blocks = b""
        counter = 0
        while len(blocks) < length:
            material = self._key + nonce + counter.to_bytes(4, "big")
            blocks += hashlib.sha256(material).digest()
            counter += 1
        return blocks[:length]

    def _tag(self, nonce: bytes, ciphertext: bytes, associated_data: bytes | None) -> bytes:
        material = b"tag:" + nonce + b":" + (associated_data or b"") + b":" + ciphertext
        return hmac.new(self._key, material, hashlib.sha256).digest()[:16]

    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        stream = self._stream(nonce, len(data))
        ciphertext = bytes(a ^ b for a, b in zip(data, stream, strict=True))
        return ciphertext + self._tag(nonce, ciphertext, associated_data)

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        ciphertext, tag = data[:-16], data[-16:]
        if not hmac.compare_digest(tag, self._tag(nonce, ciphertext, associated_data)):
            raise ValueError("authentication tag verification failed")
        stream = self._stream(nonce, len(ciphertext))
        return bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True))


@pytest.fixture(autouse=True)
def _aesgcm_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        importlib.import_module(_AEAD_MODULE)
    except ImportError:
        aead = types.ModuleType(_AEAD_MODULE)
        aead.AESGCM = _StdlibAesGcmDouble  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, _AEAD_MODULE, aead)


def _enabled_workspace(root: Path, **flag_overrides: bool) -> Path:
    return make_platform_workspace(
        root,
        flag_overrides={"restricted_encryption_enabled": True, **flag_overrides},
    )


def _service(root: Path, monkeypatch: pytest.MonkeyPatch) -> SecretEncryptionService:
    monkeypatch.setenv(KEY_VARIABLE, base64.b64encode(b"\x01" * 32).decode("ascii"))
    return SecretEncryptionService(root, provider=EnvironmentKeyProvider(variable=KEY_VARIABLE))


def _decrypt_approval(root: Path, blob: EncryptedBlob) -> ApprovalRecord:
    return store_approval(
        root,
        action="decrypt_secret",
        target_id=blob.blob_id,
        artifact_sha256=sha256_hex(canonical_json_bytes(blob)),
        scope=("secret_decrypt",),
    )


def test_round_trip_requires_exact_fresh_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _enabled_workspace(tmp_path)
    service = _service(root, monkeypatch)
    blob = service.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    approval = _decrypt_approval(root, blob)
    plaintext = service.decrypt(blob, associated_data=ASSOCIATED, approval_id=approval.approval_id)
    assert plaintext == PLAINTEXT
    with pytest.raises(SecretEncryptionError, match="fresh approval"):
        service.decrypt(blob, associated_data=ASSOCIATED, approval_id="")
    wrong_scope = store_approval(
        root,
        action="decrypt_secret",
        target_id=blob.blob_id,
        artifact_sha256=sha256_hex(canonical_json_bytes(blob)),
        scope=("eval_baseline",),
    )
    with pytest.raises(SecretEncryptionError, match="authority"):
        service.decrypt(blob, associated_data=ASSOCIATED, approval_id=wrong_scope.approval_id)
    wrong_target = store_approval(
        root,
        action="decrypt_secret",
        target_id="eblob_other",
        artifact_sha256="0" * 64,
        scope=("secret_decrypt",),
    )
    with pytest.raises(SecretEncryptionError, match="authority"):
        service.decrypt(blob, associated_data=ASSOCIATED, approval_id=wrong_target.approval_id)
    with pytest.raises(SecretEncryptionError, match="associated data"):
        service.decrypt(blob, associated_data=b"other", approval_id=approval.approval_id)


def test_corrupted_encrypted_blob_fails_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _enabled_workspace(tmp_path)
    service = _service(root, monkeypatch)
    blob = service.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    raw = base64.b64decode(blob.ciphertext)
    flipped = bytes([raw[0] ^ 0x01]) + raw[1:]
    tampered = blob.model_copy(update={"ciphertext": base64.b64encode(flipped).decode("ascii")})
    approval = _decrypt_approval(root, tampered)
    with pytest.raises(SecretEncryptionError, match="authentication failed"):
        service.decrypt(tampered, associated_data=ASSOCIATED, approval_id=approval.approval_id)


def test_missing_key_provider_is_honestly_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _enabled_workspace(tmp_path)
    blob = _service(root, monkeypatch).encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    service = SecretEncryptionService(root, provider=UnavailableKeyProvider())
    status = service.status()
    assert status.state is KeyProviderState.UNAVAILABLE
    assert "key_provider_not_configured" in status.reason_codes
    assert status.key_id is None and status.algorithm is None
    with pytest.raises(SecretEncryptionError, match="unavailable"):
        service.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    approval = _decrypt_approval(root, blob)
    with pytest.raises(SecretEncryptionError, match="unavailable"):
        service.decrypt(blob, associated_data=ASSOCIATED, approval_id=approval.approval_id)


def test_restricted_encryption_disabled_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled_root = _enabled_workspace(tmp_path / "enabled")
    blob = _service(enabled_root, monkeypatch).encrypt(
        PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID
    )
    disabled_root = make_platform_workspace(tmp_path / "disabled")
    service = _service(disabled_root, monkeypatch)
    status = service.status()
    assert status.state is KeyProviderState.UNAVAILABLE
    assert status.reason_codes == ("restricted_encryption_disabled",)
    with pytest.raises(SecretEncryptionError, match="unavailable"):
        service.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    approval = _decrypt_approval(disabled_root, blob)
    with pytest.raises(SecretEncryptionError, match="unavailable"):
        service.decrypt(blob, associated_data=ASSOCIATED, approval_id=approval.approval_id)


def test_rotation_replaces_ciphertext_and_bumps_key_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _enabled_workspace(tmp_path)
    service = _service(root, monkeypatch)
    relative = "ops/encrypted/token.json"
    old = service.encrypt_to_path(relative, PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    approval = _decrypt_approval(root, old)
    with pytest.raises(SecretEncryptionError, match="actor"):
        service.rotate(
            relative,
            associated_data=ASSOCIATED,
            approval_id=approval.approval_id,
            actor_id="",
        )
    with pytest.raises(SecretEncryptionError, match="approval"):
        service.rotate(
            relative,
            associated_data=ASSOCIATED,
            approval_id="",
            actor_id="user_local_test",
        )
    rotated = service.rotate(
        relative,
        associated_data=ASSOCIATED,
        approval_id=approval.approval_id,
        actor_id="user_local_test",
    )
    assert rotated.blob_id != old.blob_id
    assert rotated.nonce != old.nonce
    assert rotated.extensions["key_version"] == 2
    assert rotated.extensions["rotated_by"] == "user_local_test"
    persisted = (root / "ops" / "encrypted" / "token.json").read_text(encoding="utf-8")
    assert old.ciphertext not in persisted
    assert rotated.ciphertext in persisted
    assert EncryptedBlob.model_validate_json(persisted).blob_id == rotated.blob_id
    with pytest.raises(SecretEncryptionError, match="authority"):
        service.decrypt(rotated, associated_data=ASSOCIATED, approval_id=approval.approval_id)
    fresh = _decrypt_approval(root, rotated)
    assert (
        service.decrypt(rotated, associated_data=ASSOCIATED, approval_id=fresh.approval_id)
        == PLAINTEXT
    )
    twice = service.rotate(
        rotated,
        associated_data=ASSOCIATED,
        approval_id=fresh.approval_id,
        actor_id="user_local_test",
    )
    assert twice.extensions["key_version"] == 3
    for path in root.rglob("*"):
        if path.is_file():
            assert PLAINTEXT not in path.read_bytes()


def test_external_kms_stays_fail_closed(tmp_path: Path) -> None:
    flag_off = _enabled_workspace(tmp_path / "flag_off")
    service = SecretEncryptionService(flag_off, provider=ExternalKmsKeyProvider())
    status = service.status()
    assert status.state is KeyProviderState.UNAVAILABLE
    assert status.reason_codes == ("external_kms_disabled",)
    assert status.external is True
    with pytest.raises(SecretEncryptionError, match="unavailable"):
        service.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id="key_external")
    flag_on = _enabled_workspace(tmp_path / "flag_on", external_kms_enabled=True)
    stub = SecretEncryptionService(flag_on, provider=ExternalKmsKeyProvider())
    stub_status = stub.status()
    assert stub_status.state is KeyProviderState.UNAVAILABLE
    assert "external_kms_not_implemented" in stub_status.reason_codes
    with pytest.raises(SecretEncryptionError, match="unavailable"):
        stub.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id="key_external")


def test_equal_plaintexts_never_share_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _enabled_workspace(tmp_path)
    service = _service(root, monkeypatch)
    first = service.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    second = service.encrypt(PLAINTEXT, associated_data=ASSOCIATED, key_id=KEY_ID)
    assert first.blob_id != second.blob_id
    assert first.plaintext_sha256 != second.plaintext_sha256
    assert sha256_hex(PLAINTEXT) not in {first.plaintext_sha256, second.plaintext_sha256}


def test_macos_keychain_claims_available_only_after_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MacOSKeychainProvider()
    monkeypatch.setattr("raytsystem.secrets.service.shutil.which", lambda name: None)
    assert provider.status().state is KeyProviderState.UNAVAILABLE

    monkeypatch.setattr("raytsystem.secrets.service.shutil.which", lambda name: "/usr/bin/security")

    def broken_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("keychain is locked")

    monkeypatch.setattr("raytsystem.secrets.service.subprocess.run", broken_run)
    broken = provider.status()
    assert broken.state is KeyProviderState.UNAVAILABLE
    assert "macos_keychain_roundtrip_failed" in broken.reason_codes

    calls: list[str] = []
    material = base64.b64encode(b"\x02" * 32)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command[1])
        if command[1] == "find-generic-password":
            if "add-generic-password" in calls:
                return subprocess.CompletedProcess(command, 0, stdout=material + b"\n", stderr=b"")
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"not found")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("raytsystem.secrets.service.subprocess.run", fake_run)
    probed = provider.status()
    assert probed.state is KeyProviderState.AVAILABLE
    assert calls == ["find-generic-password", "add-generic-password", "find-generic-password"]
    assert provider.key("key_macos_keychain") == b"\x02" * 32
