from __future__ import annotations

import base64
import importlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

from pydantic import ValidationError

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import (
    EncryptedBlob,
    KeyProviderStatus,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.lifecycle import KeyProviderState
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.io import write_text_atomic
from raytsystem.security.paths import PathPolicyError, read_regular_file

_INTEGRITY_DOMAIN = "raytsystem.encrypted_blob.integrity.v1"
_MAX_BLOB_BYTES = 1_048_576


class SecretEncryptionError(RuntimeError):
    """Restricted encryption is unavailable or authenticated decryption failed."""


def _integrity_sha256(key_id: str, nonce: str, plaintext: bytes) -> str:
    # Bind the integrity hash to the blob identity so equal plaintexts never
    # produce equal identifiers across blobs.
    material = {
        "domain": _INTEGRITY_DOMAIN,
        "key_id": key_id,
        "nonce": nonce,
        "plaintext_sha256": sha256_hex(plaintext),
    }
    return sha256_hex(canonical_json_bytes(material))


def _key_version(blob: EncryptedBlob) -> int:
    version: object = blob.extensions.get("key_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SecretEncryptionError("Encrypted blob key version is invalid")
    return int(version)


def _encrypted_relative(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("ops", "encrypted"):
        raise SecretEncryptionError("Encrypted blobs must stay under ops/encrypted")
    return path


class KeyProvider(Protocol):
    provider_id: str
    kind: str

    def status(self) -> KeyProviderStatus: ...

    def key(self, key_id: str) -> bytes: ...


@dataclass(frozen=True)
class UnavailableKeyProvider:
    provider_id: str = "key_provider_unavailable"
    kind: str = "os_keychain"
    reason: str = "key_provider_not_configured"

    def status(self) -> KeyProviderStatus:
        return KeyProviderStatus(
            provider_id=self.provider_id,
            kind="os_keychain",
            state=KeyProviderState.UNAVAILABLE,
            reason_codes=(self.reason,),
            external=False,
        )

    def key(self, key_id: str) -> bytes:
        del key_id
        raise SecretEncryptionError("Key provider is unavailable")


@dataclass(frozen=True)
class EnvironmentKeyProvider:
    variable: str
    provider_id: str = "key_provider_environment"
    kind: str = "environment"

    def status(self) -> KeyProviderStatus:
        configured = bool(os.environ.get(self.variable)) and _aesgcm_available()
        return KeyProviderStatus(
            provider_id=self.provider_id,
            kind="environment",
            state=KeyProviderState.AVAILABLE if configured else KeyProviderState.UNAVAILABLE,
            key_id="key_environment" if configured else None,
            algorithm="aes-256-gcm" if configured else None,
            reason_codes=() if configured else ("environment_key_unavailable",),
            external=False,
        )

    def key(self, key_id: str) -> bytes:
        if key_id != "key_environment":
            raise SecretEncryptionError("Environment key ID is unknown")
        value = os.environ.get(self.variable)
        if value is None:
            raise SecretEncryptionError("Environment key is unavailable")
        try:
            key = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise SecretEncryptionError("Environment key encoding is invalid") from error
        if len(key) != 32:
            raise SecretEncryptionError("Environment key must contain 32 bytes")
        return key


@dataclass(frozen=True)
class MacOSKeychainProvider:
    service: str = "raytsystem.restricted"
    account: str = "workspace"
    provider_id: str = "key_provider_macos_keychain"
    kind: str = "os_keychain"

    def status(self) -> KeyProviderStatus:
        # AVAILABLE requires a proven keychain round-trip, never a binary on PATH.
        available = _aesgcm_available() and self._probe()
        return KeyProviderStatus(
            provider_id=self.provider_id,
            kind="os_keychain",
            state=KeyProviderState.AVAILABLE if available else KeyProviderState.UNAVAILABLE,
            key_id="key_macos_keychain" if available else None,
            algorithm="aes-256-gcm" if available else None,
            reason_codes=() if available else ("macos_keychain_roundtrip_failed",),
            external=False,
        )

    def key(self, key_id: str) -> bytes:
        if key_id != "key_macos_keychain":
            raise SecretEncryptionError("macOS Keychain key ID is unknown")
        key = self._find_key()
        if key is None:
            raise SecretEncryptionError("macOS Keychain key is not configured")
        return key

    def _probe(self) -> bool:
        if shutil.which("security") is None:
            return False
        try:
            if self._find_key() is None:
                self._add_key()
            return self._find_key() is not None
        except SecretEncryptionError:
            return False

    def _find_key(self) -> bytes | None:
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    self.service,
                    "-a",
                    self.account,
                    "-w",
                ],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SecretEncryptionError("macOS Keychain probe failed") from error
        if result.returncode != 0:
            return None
        try:
            key = base64.b64decode(result.stdout.strip(), validate=True)
        except ValueError as error:
            raise SecretEncryptionError("macOS Keychain key encoding is invalid") from error
        if len(key) != 32:
            raise SecretEncryptionError("macOS Keychain key must contain 32 bytes")
        return key

    def _add_key(self) -> None:
        material = base64.b64encode(os.urandom(32)).decode("ascii")
        try:
            result = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-s",
                    self.service,
                    "-a",
                    self.account,
                    "-w",
                    material,
                ],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SecretEncryptionError("macOS Keychain marker entry could not be added") from error
        if result.returncode != 0:
            raise SecretEncryptionError("macOS Keychain marker entry could not be added")


@dataclass(frozen=True)
class ExternalKmsKeyProvider:
    """Fail-closed stub: external KMS is declared but never claims encryption capability."""

    provider_id: str = "key_provider_external_kms"
    kind: str = "external_kms"

    def status(self) -> KeyProviderStatus:
        return KeyProviderStatus(
            provider_id=self.provider_id,
            kind="external_kms",
            state=KeyProviderState.UNAVAILABLE,
            reason_codes=("external_kms_not_implemented",),
            external=True,
        )

    def key(self, key_id: str) -> bytes:
        del key_id
        raise SecretEncryptionError("External KMS key provider is not implemented")


class SecretEncryptionService:
    def __init__(
        self,
        root: Path,
        *,
        provider: KeyProvider | None = None,
        features: FeatureConfig | None = None,
    ) -> None:
        self.root = root.resolve()
        self.features = features or load_feature_config(self.root)
        self.provider = provider or MacOSKeychainProvider()

    def status(self) -> KeyProviderStatus:
        if not self.features.enabled("restricted_encryption_enabled"):
            return self._gated_status(("restricted_encryption_disabled",))
        if self.provider.kind == "external_kms" and not self.features.enabled(
            "external_kms_enabled"
        ):
            return self._gated_status(("external_kms_disabled",))
        return self.provider.status()

    def encrypt(
        self,
        plaintext: bytes,
        *,
        associated_data: bytes,
        key_id: str,
    ) -> EncryptedBlob:
        return self._encrypt(
            plaintext,
            associated_data=associated_data,
            key_id=key_id,
            extensions={"key_version": 1},
        )

    def decrypt(
        self,
        blob: EncryptedBlob,
        *,
        associated_data: bytes,
        approval_id: str,
    ) -> bytes:
        if not approval_id:
            raise SecretEncryptionError("Decrypt requires a fresh approval")
        provider_status = self.status()
        if provider_status.state is not KeyProviderState.AVAILABLE:
            raise SecretEncryptionError("Restricted encryption is unavailable")
        if (
            blob.key_provider_id != self.provider.provider_id
            or provider_status.key_id != blob.key_id
            or provider_status.algorithm != blob.algorithm
        ):
            raise SecretEncryptionError("Encrypted blob key-provider binding is invalid")
        try:
            AuthorityResolver(self.root).require_approval(
                approval_id,
                action="decrypt_secret",
                target_id=blob.blob_id,
                artifact_sha256=sha256_hex(canonical_json_bytes(blob)),
                required_scope=frozenset({"secret_decrypt"}),
            )
        except AuthorityError as error:
            raise SecretEncryptionError("Decrypt approval authority is invalid") from error
        return self._open(blob, associated_data=associated_data)

    def rotate(
        self,
        blob: EncryptedBlob | str,
        *,
        associated_data: bytes,
        approval_id: str,
        actor_id: str,
    ) -> EncryptedBlob:
        if not actor_id:
            raise SecretEncryptionError("Rotate requires an actor identity")
        if isinstance(blob, str):
            return self._rotate_path(
                blob,
                associated_data=associated_data,
                approval_id=approval_id,
                actor_id=actor_id,
            )
        return self._rotate_blob(
            blob,
            associated_data=associated_data,
            approval_id=approval_id,
            actor_id=actor_id,
        )

    def encrypt_to_path(
        self,
        relative: str,
        plaintext: bytes,
        *,
        associated_data: bytes,
        key_id: str,
    ) -> EncryptedBlob:
        path = _encrypted_relative(relative)
        blob = self.encrypt(plaintext, associated_data=associated_data, key_id=key_id)
        target = self.root.joinpath(*path.parts)
        write_text_atomic(target, canonical_json_bytes(blob).decode("utf-8") + "\n", mode=0o600)
        return blob

    def _rotate_blob(
        self,
        old: EncryptedBlob,
        *,
        associated_data: bytes,
        approval_id: str,
        actor_id: str,
    ) -> EncryptedBlob:
        plaintext = self.decrypt(old, associated_data=associated_data, approval_id=approval_id)
        rotated = self._encrypt(
            plaintext,
            associated_data=associated_data,
            key_id=old.key_id,
            extensions={"key_version": _key_version(old) + 1, "rotated_by": actor_id},
        )
        if rotated.nonce == old.nonce or rotated.ciphertext == old.ciphertext:
            raise SecretEncryptionError("Rotation must produce fresh encryption material")
        if self._open(rotated, associated_data=associated_data) != plaintext:
            raise SecretEncryptionError("Rotated blob failed verification")
        return rotated

    def _rotate_path(
        self,
        relative: str,
        *,
        associated_data: bytes,
        approval_id: str,
        actor_id: str,
    ) -> EncryptedBlob:
        path = _encrypted_relative(relative)
        try:
            data = read_regular_file(self.root, relative, max_bytes=_MAX_BLOB_BYTES).data
        except (OSError, PathPolicyError) as error:
            raise SecretEncryptionError("Encrypted blob file is unavailable") from error
        try:
            old = EncryptedBlob.model_validate_json(data)
        except ValidationError as error:
            raise SecretEncryptionError("Encrypted blob file is invalid") from error
        rotated = self._rotate_blob(
            old,
            associated_data=associated_data,
            approval_id=approval_id,
            actor_id=actor_id,
        )
        # The old blob bytes are replaced only after the rotated blob verified.
        target = self.root.joinpath(*path.parts)
        write_text_atomic(target, canonical_json_bytes(rotated).decode("utf-8") + "\n", mode=0o600)
        return rotated

    def _encrypt(
        self,
        plaintext: bytes,
        *,
        associated_data: bytes,
        key_id: str,
        extensions: dict[str, Any],
    ) -> EncryptedBlob:
        self._require_available()
        AESGCM = _aesgcm()
        wrapping_key = self.provider.key(key_id)
        data_key = os.urandom(32)
        data_nonce = os.urandom(12)
        key_nonce = os.urandom(12)
        encrypted = AESGCM(data_key).encrypt(data_nonce, plaintext, associated_data)
        ciphertext, tag = encrypted[:-16], encrypted[-16:]
        wrapped_key = AESGCM(wrapping_key).encrypt(key_nonce, data_key, associated_data)
        encrypted_data_key = base64.b64encode(key_nonce + wrapped_key).decode("ascii")
        nonce = base64.b64encode(data_nonce).decode("ascii")
        encoded_ciphertext = base64.b64encode(ciphertext).decode("ascii")
        authentication_tag = base64.b64encode(tag).decode("ascii")
        plaintext_sha256 = _integrity_sha256(key_id, nonce, plaintext)
        associated_data_sha256 = sha256_hex(associated_data)
        return EncryptedBlob(
            blob_id=derive_id(
                "eblob",
                {
                    "key_id": key_id,
                    "plaintext_sha256": plaintext_sha256,
                    "ciphertext_sha256": sha256_hex(ciphertext),
                    "nonce": nonce,
                },
            ),
            key_provider_id=self.provider.provider_id,
            key_id=key_id,
            algorithm="aes-256-gcm",
            algorithm_version="1",
            encrypted_data_key=encrypted_data_key,
            nonce=nonce,
            ciphertext=encoded_ciphertext,
            authentication_tag=authentication_tag,
            plaintext_sha256=plaintext_sha256,
            associated_data_sha256=associated_data_sha256,
            created_at=datetime.now(UTC),
            extensions=dict(extensions),
        )

    def _open(self, blob: EncryptedBlob, *, associated_data: bytes) -> bytes:
        if sha256_hex(associated_data) != blob.associated_data_sha256:
            raise SecretEncryptionError("Encrypted blob associated data does not match")
        AESGCM = _aesgcm()
        try:
            wrapping_key = self.provider.key(blob.key_id)
            wrapped = base64.b64decode(blob.encrypted_data_key, validate=True)
            data_key = AESGCM(wrapping_key).decrypt(wrapped[:12], wrapped[12:], associated_data)
            nonce = base64.b64decode(blob.nonce, validate=True)
            ciphertext = base64.b64decode(blob.ciphertext, validate=True)
            tag = base64.b64decode(blob.authentication_tag, validate=True)
            plaintext = cast(
                bytes, AESGCM(data_key).decrypt(nonce, ciphertext + tag, associated_data)
            )
        except SecretEncryptionError:
            raise
        except Exception as error:
            raise SecretEncryptionError("Encrypted blob authentication failed") from error
        if _integrity_sha256(blob.key_id, blob.nonce, plaintext) != blob.plaintext_sha256:
            raise SecretEncryptionError("Encrypted blob integrity hash failed")
        return plaintext

    def _gated_status(self, reason_codes: tuple[str, ...]) -> KeyProviderStatus:
        # Feature gates must not probe (or mutate) any key backend as a side effect.
        return KeyProviderStatus.model_validate(
            {
                "provider_id": self.provider.provider_id,
                "kind": self.provider.kind,
                "state": KeyProviderState.UNAVAILABLE,
                "reason_codes": reason_codes,
                "external": self.provider.kind == "external_kms",
            }
        )

    def _require_available(self) -> None:
        status = self.status()
        if status.state is not KeyProviderState.AVAILABLE:
            raise SecretEncryptionError("Restricted encryption is unavailable")


def _aesgcm_available() -> bool:
    try:
        _aesgcm()
    except SecretEncryptionError:
        return False
    return True


def _aesgcm() -> type[Any]:
    try:
        module = importlib.import_module("cryptography.hazmat.primitives.ciphers.aead")
        aesgcm = module.__dict__["AESGCM"]
    except (ImportError, AttributeError) as error:
        raise SecretEncryptionError("AES-GCM backend is unavailable") from error
    return cast(type[Any], aesgcm)
