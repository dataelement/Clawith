"""Durable, scope-bound Workspace candidate reconciliation.

This module stores only reconciliation evidence and candidate bytes.  It does
not own, infer, or mutate Agent Run lifecycle state.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.services.storage_runtime.base import StorageBackend, StorageVersion, WriteCondition
from app.services.workspace_locking import workspace_locks

BaseState = Literal["present", "absent", "unloaded"]
CandidateOperation = Literal["create", "replace", "delete"]
VerificationStatus = Literal["applied", "not_saved", "conflict", "unverified"]
ApplyStatus = Literal["applied", "already_applied", "conflict", "unverified"]

_SCOPE_COMPONENT = re.compile(r"^[A-Za-z0-9_.:-]+$")
_PRIVATE_ROOT = "private/workspace-reconciliation"
_MANIFEST_VERSION = 1


class LockFactory(Protocol):
    def __call__(
        self,
        agent_id: uuid.UUID,
        paths: list[str],
        *,
        tenant_id: str,
    ): ...


@dataclass(frozen=True)
class ReconciliationScope:
    tenant_id: str
    agent_id: uuid.UUID
    run_id: str
    execution_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, uuid.UUID):
            raise TypeError("agent_id must be a UUID")
        for name in ("tenant_id", "run_id", "execution_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value in {".", ".."} or not _SCOPE_COMPONENT.fullmatch(value):
                raise ValueError(f"invalid {name} scope component")


@dataclass(frozen=True)
class CandidateChange:
    path: str
    operation: CandidateOperation
    base_state: BaseState
    data: bytes | None = None
    base_version: str | None = None
    base_hash: str | None = None

    @classmethod
    def create(cls, path: str, data: bytes) -> CandidateChange:
        return cls(path=path, operation="create", base_state="absent", data=data)

    @classmethod
    def replace(
        cls,
        path: str,
        data: bytes,
        *,
        base_version: str | None = None,
        base_hash: str | None = None,
    ) -> CandidateChange:
        return cls(
            path=path,
            operation="replace",
            base_state="present",
            data=data,
            base_version=base_version,
            base_hash=base_hash,
        )

    @classmethod
    def delete(
        cls,
        path: str,
        *,
        base_version: str | None = None,
        base_hash: str | None = None,
    ) -> CandidateChange:
        return cls(
            path=path,
            operation="delete",
            base_state="present",
            base_version=base_version,
            base_hash=base_hash,
        )


@dataclass(frozen=True)
class CandidateManifestChange:
    path: str
    operation: CandidateOperation
    base_state: BaseState
    base_version: str | None
    base_hash: str | None
    candidate_hash: str | None
    candidate_ref: str | None


@dataclass(frozen=True)
class CandidateManifest:
    candidate_ref: str
    tenant_id: str
    agent_id: str
    run_id: str
    execution_id: str
    changes: tuple[CandidateManifestChange, ...]
    schema_version: int = _MANIFEST_VERSION


@dataclass(frozen=True)
class ChangeVerification:
    path: str
    operation: CandidateOperation
    status: VerificationStatus
    current_hash: str | None = None
    current_version: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    status: Literal["applied", "not_saved", "needs_resolution", "unverified", "mixed"]
    counts: dict[str, int]
    changes: tuple[ChangeVerification, ...]


@dataclass(frozen=True)
class ChangeApplication:
    path: str
    operation: CandidateOperation
    status: ApplyStatus
    detail: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    status: ApplyStatus
    changes: tuple[ChangeApplication, ...]


def expand_move(
    *,
    source_path: str,
    destination_path: str,
    data: bytes,
    source_base_version: str | None = None,
    source_base_hash: str | None = None,
    destination_base_state: BaseState,
    destination_base_version: str | None = None,
    destination_base_hash: str | None = None,
) -> tuple[CandidateChange, CandidateChange]:
    """Expand a move into destination write followed by source deletion."""
    destination_operation: CandidateOperation = "create" if destination_base_state == "absent" else "replace"
    return (
        CandidateChange(
            path=destination_path,
            operation=destination_operation,
            base_state=destination_base_state,
            data=data,
            base_version=destination_base_version,
            base_hash=destination_base_hash,
        ),
        CandidateChange.delete(
            source_path,
            base_version=source_base_version,
            base_hash=source_base_hash,
        ),
    )


class WorkspaceReconciliationService:
    """Persist, verify, apply, and discard one execution-scoped candidate."""

    def __init__(self, storage: StorageBackend, *, lock_factory: LockFactory = workspace_locks) -> None:
        self.storage = storage
        self.lock_factory = lock_factory

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def persist_candidate(
        self,
        scope: ReconciliationScope,
        changes: Sequence[CandidateChange],
    ) -> CandidateManifest:
        prefix = self._scope_prefix(scope)
        manifest_ref = f"{prefix}/manifest.json"
        normalized_changes = [self._validate_change(change) for change in changes]
        if not normalized_changes:
            raise ValueError("candidate must contain at least one change")
        paths = [change.path for change in normalized_changes]
        if len(paths) != len(set(paths)):
            raise ValueError("candidate paths must be unique")

        if (await self.storage.get_version(manifest_ref)).exists:
            existing = await self._load_manifest(scope, manifest_ref)
            expected = self._build_manifest(scope, manifest_ref, normalized_changes)
            if existing == expected:
                return existing
            raise ValueError("execution scope already owns a different candidate")

        manifest = self._build_manifest(scope, manifest_ref, normalized_changes)
        for source, stored in zip(normalized_changes, manifest.changes, strict=True):
            if stored.candidate_ref is not None:
                assert source.data is not None
                await self.storage.write_bytes(stored.candidate_ref, source.data)
        write_result = await self.storage.write_bytes_if_match(
            manifest_ref,
            self._manifest_bytes(manifest),
            condition=WriteCondition(require_absent=True),
            content_type="application/json",
        )
        if not write_result.ok:
            existing = await self._load_manifest(scope, manifest_ref)
            if existing == manifest:
                return existing
            raise ValueError("execution scope concurrently created a different candidate")
        return manifest

    async def verify_current(self, scope: ReconciliationScope, candidate_ref: str) -> VerificationResult:
        manifest = await self._load_manifest(scope, candidate_ref)
        changes = tuple([await self._verify_change(scope, change) for change in manifest.changes])
        counts = Counter(change.status for change in changes)
        normalized_counts = {
            status: counts.get(status, 0) for status in ("applied", "not_saved", "conflict", "unverified")
        }
        if normalized_counts["conflict"]:
            status = "needs_resolution"
        elif normalized_counts["unverified"]:
            status = "unverified"
        elif normalized_counts["applied"] == len(changes):
            status = "applied"
        elif normalized_counts["not_saved"] == len(changes):
            status = "not_saved"
        else:
            status = "mixed"
        return VerificationResult(status=status, counts=normalized_counts, changes=changes)

    async def apply_candidate(
        self,
        scope: ReconciliationScope,
        candidate_ref: str,
        *,
        authorized: bool,
        require_base_match: bool = False,
    ) -> ApplyResult:
        if not authorized:
            raise PermissionError("candidate apply requires explicit authorization")
        manifest = await self._load_manifest(scope, candidate_ref)
        # Capture the review-time view first.  The locked snapshots below are
        # intentionally fresh and are the only versions used for mutation CAS.
        for change in manifest.changes:
            await self._verify_change(scope, change)
        candidate_bytes = await self._load_candidate_bytes(scope, manifest)
        paths = [change.path for change in manifest.changes]

        async with self.lock_factory(
            scope.agent_id,
            paths,
            tenant_id=scope.tenant_id,
        ):
            snapshots: dict[str, tuple[StorageVersion, str | None]] = {}
            for change in manifest.changes:
                try:
                    snapshots[change.path] = await self._read_current(scope, change.path)
                # Storage adapters may surface provider-specific read errors.
                except Exception as exc:  # noqa: BLE001
                    results = tuple(
                        ChangeApplication(item.path, item.operation, "unverified", type(exc).__name__)
                        for item in manifest.changes
                    )
                    return ApplyResult(status="unverified", changes=results)

            results: list[ChangeApplication] = []
            ordered = sorted(manifest.changes, key=lambda change: change.operation == "delete")
            for change in ordered:
                version, current_hash = snapshots[change.path]
                already_applied = (change.operation == "delete" and not version.exists) or (
                    change.operation != "delete" and current_hash == change.candidate_hash
                )
                if already_applied:
                    results.append(ChangeApplication(change.path, change.operation, "already_applied"))
                    continue
                if require_base_match:
                    base_status = self._compare_with_base(change, version, current_hash)
                    if base_status == "unverified":
                        results.append(
                            ChangeApplication(
                                change.path,
                                change.operation,
                                "unverified",
                                "base_state_unverified",
                            )
                        )
                        return ApplyResult(status="unverified", changes=tuple(results))
                    if base_status != "not_saved":
                        results.append(
                            ChangeApplication(
                                change.path,
                                change.operation,
                                "conflict",
                                "version_changed",
                            )
                        )
                        return ApplyResult(status="conflict", changes=tuple(results))
                condition = (
                    WriteCondition(version_token=version.token)
                    if version.exists
                    else WriteCondition(require_absent=True)
                )
                storage_key = self._workspace_key(scope, change.path)
                if change.operation == "delete":
                    mutation = await self.storage.delete_if_match(storage_key, condition=condition)
                else:
                    mutation = await self.storage.write_bytes_if_match(
                        storage_key,
                        candidate_bytes[change.path],
                        condition=condition,
                    )
                if not mutation.ok:
                    results.append(ChangeApplication(change.path, change.operation, "conflict", "version_changed"))
                    return ApplyResult(status="conflict", changes=tuple(results))
                results.append(ChangeApplication(change.path, change.operation, "applied"))

        status: ApplyStatus = (
            "already_applied" if results and all(item.status == "already_applied" for item in results) else "applied"
        )
        return ApplyResult(status=status, changes=tuple(results))

    async def preserve_conflicts_and_apply_safe_changes(
        self,
        scope: ReconciliationScope,
        candidate_ref: str,
    ) -> VerificationResult:
        """Keep third-party versions while publishing independent safe writes.

        Deletes are intentionally skipped whenever any path is conflicted or
        unreadable. A delete may be the source half of a move, so applying it
        after preserving a conflicting destination could lose the only copy.
        """
        manifest = await self._load_manifest(scope, candidate_ref)
        candidate_bytes = await self._load_candidate_bytes(scope, manifest)
        paths = [change.path for change in manifest.changes]

        async with self.lock_factory(
            scope.agent_id,
            paths,
            tenant_id=scope.tenant_id,
        ):
            snapshots: dict[str, tuple[StorageVersion, str | None] | None] = {}
            statuses: dict[str, VerificationStatus] = {}
            for change in manifest.changes:
                try:
                    snapshot = await self._read_current(scope, change.path)
                except Exception:  # noqa: BLE001 - unreadable paths stay untouched
                    snapshots[change.path] = None
                    statuses[change.path] = "unverified"
                    continue
                snapshots[change.path] = snapshot
                version, current_hash = snapshot
                if (change.operation == "delete" and not version.exists) or (
                    change.operation != "delete" and current_hash == change.candidate_hash
                ):
                    statuses[change.path] = "applied"
                else:
                    statuses[change.path] = self._compare_with_base(
                        change,
                        version,
                        current_hash,
                    )

            has_unsafe_path = any(status in {"conflict", "unverified"} for status in statuses.values())
            ordered = sorted(
                manifest.changes,
                key=lambda change: change.operation == "delete",
            )
            for change in ordered:
                if statuses[change.path] != "not_saved":
                    continue
                if change.operation == "delete" and has_unsafe_path:
                    continue
                snapshot = snapshots[change.path]
                if snapshot is None:
                    continue
                version, _current_hash = snapshot
                condition = (
                    WriteCondition(version_token=version.token)
                    if version.exists
                    else WriteCondition(require_absent=True)
                )
                storage_key = self._workspace_key(scope, change.path)
                if change.operation == "delete":
                    await self.storage.delete_if_match(
                        storage_key,
                        condition=condition,
                    )
                else:
                    await self.storage.write_bytes_if_match(
                        storage_key,
                        candidate_bytes[change.path],
                        condition=condition,
                    )

        return await self.verify_current(scope, candidate_ref)

    async def discard_candidate(self, scope: ReconciliationScope, candidate_ref: str) -> None:
        expected_ref = f"{self._scope_prefix(scope)}/manifest.json"
        if candidate_ref != expected_ref:
            raise ValueError("candidate_ref does not belong to scope")
        await self.storage.delete_tree(self._scope_prefix(scope))

    async def cleanup_candidates(self, scope: ReconciliationScope) -> None:
        await self.storage.delete_tree(self._scope_prefix(scope))

    async def cleanup_run_candidates(
        self,
        *,
        tenant_id: str,
        agent_id: uuid.UUID,
        run_id: str,
    ) -> None:
        """Remove every private candidate after its owning Run is terminal."""
        for name, value in {
            "tenant_id": tenant_id,
            "run_id": run_id,
        }.items():
            if not value or value in {".", ".."} or not _SCOPE_COMPONENT.fullmatch(value):
                raise ValueError(f"invalid {name} scope component")
        await self.storage.delete_tree(
            f"{_PRIVATE_ROOT}/{tenant_id}/{agent_id}/{run_id}"
        )

    def _build_manifest(
        self,
        scope: ReconciliationScope,
        manifest_ref: str,
        changes: Sequence[CandidateChange],
    ) -> CandidateManifest:
        prefix = self._scope_prefix(scope)
        stored: list[CandidateManifestChange] = []
        for index, change in enumerate(changes):
            candidate_hash = self.hash_bytes(change.data) if change.data is not None else None
            blob_ref = f"{prefix}/files/{index:04d}-{candidate_hash}" if candidate_hash is not None else None
            stored.append(
                CandidateManifestChange(
                    path=change.path,
                    operation=change.operation,
                    base_state=change.base_state,
                    base_version=change.base_version,
                    base_hash=change.base_hash,
                    candidate_hash=candidate_hash,
                    candidate_ref=blob_ref,
                )
            )
        return CandidateManifest(
            candidate_ref=manifest_ref,
            tenant_id=scope.tenant_id,
            agent_id=str(scope.agent_id),
            run_id=scope.run_id,
            execution_id=scope.execution_id,
            changes=tuple(stored),
        )

    def _validate_change(self, change: CandidateChange) -> CandidateChange:
        path = self._normalize_workspace_path(change.path)
        if change.operation not in {"create", "replace", "delete"}:
            raise ValueError("unsupported candidate operation")
        if change.base_state not in {"present", "absent", "unloaded"}:
            raise ValueError("unsupported candidate base_state")
        if change.operation == "delete" and change.data is not None:
            raise ValueError("delete candidate must not contain bytes")
        if change.operation != "delete" and not isinstance(change.data, bytes):
            raise ValueError("write candidate must contain bytes")
        if change.operation == "create" and change.base_state != "absent":
            raise ValueError("create candidate requires absent base_state")
        if change.base_state == "present" and change.base_hash is None and change.base_version is None:
            raise ValueError("present base_state requires base_hash or base_version")
        if change.base_state != "present" and (change.base_hash is not None or change.base_version is not None):
            raise ValueError("absent or unloaded base_state cannot claim a base version")
        return CandidateChange(
            path=path,
            operation=change.operation,
            base_state=change.base_state,
            data=change.data,
            base_version=change.base_version,
            base_hash=change.base_hash,
        )

    async def _verify_change(
        self,
        scope: ReconciliationScope,
        change: CandidateManifestChange,
    ) -> ChangeVerification:
        try:
            version, current_hash = await self._read_current(scope, change.path)
        # Read failures are evidence gaps, not storage conflicts.
        except Exception as exc:  # noqa: BLE001
            return ChangeVerification(change.path, change.operation, "unverified", detail=type(exc).__name__)
        current_version = version.token if version.exists else None
        if change.operation == "delete":
            if not version.exists:
                status: VerificationStatus = "applied"
            else:
                status = self._compare_with_base(change, version, current_hash)
        elif current_hash == change.candidate_hash:
            status = "applied"
        else:
            status = self._compare_with_base(change, version, current_hash)
        return ChangeVerification(
            path=change.path,
            operation=change.operation,
            status=status,
            current_hash=current_hash,
            current_version=current_version,
        )

    @staticmethod
    def _compare_with_base(
        change: CandidateManifestChange,
        version: StorageVersion,
        current_hash: str | None,
    ) -> VerificationStatus:
        if change.base_state == "unloaded":
            return "unverified"
        if change.base_state == "absent":
            return "not_saved" if current_hash is None else "conflict"
        hash_matches = change.base_hash is not None and current_hash == change.base_hash
        version_matches = (
            change.base_hash is None and change.base_version is not None and version.token == change.base_version
        )
        return "not_saved" if hash_matches or version_matches else "conflict"

    async def _read_current(self, scope: ReconciliationScope, path: str) -> tuple[StorageVersion, str | None]:
        storage_key = self._workspace_key(scope, path)
        version = await self.storage.get_version(storage_key)
        if not version.exists:
            return version, None
        if version.is_dir:
            raise IsADirectoryError(storage_key)
        data = await self.storage.read_bytes(storage_key)
        return version, self.hash_bytes(data)

    async def _load_candidate_bytes(
        self,
        scope: ReconciliationScope,
        manifest: CandidateManifest,
    ) -> dict[str, bytes]:
        prefix = self._scope_prefix(scope) + "/files/"
        result: dict[str, bytes] = {}
        for change in manifest.changes:
            if change.operation == "delete":
                continue
            if change.candidate_ref is None or not change.candidate_ref.startswith(prefix):
                raise ValueError("candidate file ref does not belong to scope")
            data = await self.storage.read_bytes(change.candidate_ref)
            if self.hash_bytes(data) != change.candidate_hash:
                raise ValueError("candidate bytes do not match manifest hash")
            result[change.path] = data
        return result

    async def _load_manifest(self, scope: ReconciliationScope, candidate_ref: str) -> CandidateManifest:
        expected_ref = f"{self._scope_prefix(scope)}/manifest.json"
        if candidate_ref != expected_ref:
            raise ValueError("candidate_ref does not belong to scope")
        raw = await self.storage.read_bytes(candidate_ref)
        try:
            payload = json.loads(raw)
            changes = tuple(CandidateManifestChange(**item) for item in payload.pop("changes"))
            manifest = CandidateManifest(changes=changes, **payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid candidate manifest") from exc
        if (
            manifest.schema_version != _MANIFEST_VERSION
            or manifest.candidate_ref != expected_ref
            or manifest.tenant_id != scope.tenant_id
            or manifest.agent_id != str(scope.agent_id)
            or manifest.run_id != scope.run_id
            or manifest.execution_id != scope.execution_id
        ):
            raise ValueError("candidate manifest scope mismatch")
        for index, change in enumerate(manifest.changes):
            self._validate_manifest_change(scope, change, index=index)
        return manifest

    def _validate_manifest_change(
        self,
        scope: ReconciliationScope,
        change: CandidateManifestChange,
        *,
        index: int,
    ) -> None:
        normalized = self._normalize_workspace_path(change.path)
        if normalized != change.path:
            raise ValueError("candidate manifest path is not normalized")
        if change.operation not in {"create", "replace", "delete"}:
            raise ValueError("candidate manifest operation is invalid")
        if change.base_state not in {"present", "absent", "unloaded"}:
            raise ValueError("candidate manifest base_state is invalid")
        if change.operation == "create" and change.base_state != "absent":
            raise ValueError("create manifest requires absent base_state")
        if change.base_state == "present" and change.base_hash is None and change.base_version is None:
            raise ValueError("present manifest requires base_hash or base_version")
        if change.base_state != "present" and (change.base_hash is not None or change.base_version is not None):
            raise ValueError("absent or unloaded manifest cannot claim a base version")
        if change.operation == "delete":
            if change.candidate_hash is not None or change.candidate_ref is not None:
                raise ValueError("delete manifest cannot reference candidate bytes")
            return
        prefix = self._scope_prefix(scope) + "/files/"
        expected_ref = f"{prefix}{index:04d}-{change.candidate_hash}"
        if not change.candidate_hash or not change.candidate_ref or change.candidate_ref != expected_ref:
            raise ValueError("candidate file ref does not belong to scope")

    @staticmethod
    def _manifest_bytes(manifest: CandidateManifest) -> bytes:
        payload = {
            "schema_version": manifest.schema_version,
            "candidate_ref": manifest.candidate_ref,
            "tenant_id": manifest.tenant_id,
            "agent_id": manifest.agent_id,
            "run_id": manifest.run_id,
            "execution_id": manifest.execution_id,
            "changes": [
                {
                    "path": change.path,
                    "operation": change.operation,
                    "base_state": change.base_state,
                    "base_version": change.base_version,
                    "base_hash": change.base_hash,
                    "candidate_hash": change.candidate_hash,
                    "candidate_ref": change.candidate_ref,
                }
                for change in manifest.changes
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _normalize_workspace_path(path: str) -> str:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("workspace path must not be empty")
        clean = path.replace("\\", "/").strip()
        if "\x00" in clean:
            raise ValueError("workspace path contains an invalid character")
        if clean.startswith("/"):
            raise ValueError("absolute workspace path is not allowed")
        parts = clean.split("/")
        if any(part == ".." for part in parts):
            raise ValueError("workspace path traversal is not allowed")
        normalized = "/".join(part for part in parts if part not in {"", "."})
        if not normalized:
            raise ValueError("workspace path must not be empty")
        return normalized

    @staticmethod
    def _workspace_key(scope: ReconciliationScope, path: str) -> str:
        return f"{scope.agent_id}/{path}"

    @staticmethod
    def _scope_prefix(scope: ReconciliationScope) -> str:
        return f"{_PRIVATE_ROOT}/{scope.tenant_id}/{scope.agent_id}/{scope.run_id}/{scope.execution_id}"
