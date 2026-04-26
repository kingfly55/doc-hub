from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEGACY_MANIFEST_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 2
DEFAULT_SOURCE_VERSION = "latest"


@dataclass(frozen=True)
class DocVersion:
    corpus_id: str
    snapshot_id: str
    source_version: str
    source_type: str
    source_url: str
    fetch_strategy: str
    fetch_config_hash: str
    content_hash: str
    fetched_at: str
    resolved_version: str | None = None
    url_set_hash: str | None = None
    indexed_at: str | None = None
    total_chunks: int = 0
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VersionAlias:
    corpus_id: str
    alias: str
    snapshot_id: str


@dataclass(frozen=True)
class ManifestFile:
    filename: str
    url: str = ""
    success: bool = True
    content_hash: str | None = None
    fetched_at: str | None = None
    source_version: str | None = None
    resolved_version: str | None = None
    snapshot_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> "ManifestFile":
        known = {
            "filename",
            "url",
            "success",
            "content_hash",
            "fetched_at",
            "source_version",
            "resolved_version",
            "snapshot_id",
        }
        return cls(
            filename=entry["filename"],
            url=entry.get("url", ""),
            success=entry.get("success", True),
            content_hash=entry.get("content_hash"),
            fetched_at=entry.get("fetched_at"),
            source_version=entry.get("source_version"),
            resolved_version=entry.get("resolved_version"),
            snapshot_id=entry.get("snapshot_id"),
            extra={key: value for key, value in entry.items() if key not in known},
        )

    def to_entry(self) -> dict[str, Any]:
        entry = dict(self.extra)
        entry.update({
            "filename": self.filename,
            "url": self.url,
            "success": self.success,
        })
        if self.content_hash is not None:
            entry["content_hash"] = self.content_hash
        if self.fetched_at is not None:
            entry["fetched_at"] = self.fetched_at
        if self.source_version is not None:
            entry["source_version"] = self.source_version
        if self.resolved_version is not None:
            entry["resolved_version"] = self.resolved_version
        if self.snapshot_id is not None:
            entry["snapshot_id"] = self.snapshot_id
        return entry


@dataclass(frozen=True)
class SnapshotManifest:
    schema_version: int = LEGACY_MANIFEST_SCHEMA_VERSION
    corpus_slug: str = ""
    fetch_strategy: str = ""
    source_type: str = ""
    source_url: str = ""
    source_version: str = DEFAULT_SOURCE_VERSION
    resolved_version: str | None = None
    fetched_at: str | None = None
    snapshot_id: str = ""
    url_set_hash: str | None = None
    content_hash: str | None = None
    fetch_config_hash: str | None = None
    aliases: list[str] = field(default_factory=list)
    files: dict[str, ManifestFile] = field(default_factory=dict)
    sections: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode()).hexdigest()


def hash_strings(values: list[str]) -> str:
    return hash_json(values)


def hash_manifest_files(files: dict[str, ManifestFile]) -> str:
    payload = [files[key].to_entry() for key in sorted(files)]
    return hash_json(payload)


def build_snapshot_id(
    *,
    corpus_slug: str,
    fetch_strategy: str,
    source_version: str,
    source_url: str,
    fetch_config_hash: str | None,
    url_set_hash: str | None,
    content_hash: str | None,
) -> str:
    payload = {
        "content_hash": content_hash or "",
        "corpus_slug": corpus_slug,
        "fetch_config_hash": fetch_config_hash or "",
        "fetch_strategy": fetch_strategy,
        "source_url": source_url,
        "source_version": source_version,
        "url_set_hash": url_set_hash or "",
    }
    digest = hashlib.sha256(_stable_json(payload).encode()).hexdigest()
    return f"sha256-{digest}"


def load_snapshot_manifest(input_dir: Path) -> SnapshotManifest:
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.exists():
        return SnapshotManifest()

    try:
        data = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return SnapshotManifest()

    schema_version = data.get("schema_version", LEGACY_MANIFEST_SCHEMA_VERSION)
    files = {
        entry["filename"]: ManifestFile.from_entry(entry)
        for entry in data.get("files", [])
        if entry.get("success", False) and "filename" in entry
    }

    if schema_version < MANIFEST_SCHEMA_VERSION:
        return SnapshotManifest(
            schema_version=LEGACY_MANIFEST_SCHEMA_VERSION,
            files=files,
            sections=data.get("sections", []),
            raw=data,
        )

    source = data.get("source", {})
    snapshot = data.get("snapshot", {})
    snapshot_id = snapshot.get("snapshot_id", "")
    files = {
        filename: ManifestFile(
            filename=file.filename,
            url=file.url,
            success=file.success,
            content_hash=file.content_hash,
            fetched_at=file.fetched_at,
            source_version=file.source_version,
            resolved_version=file.resolved_version,
            snapshot_id=file.snapshot_id or snapshot_id,
            extra=file.extra,
        )
        for filename, file in files.items()
    }
    return SnapshotManifest(
        schema_version=schema_version,
        corpus_slug=data.get("corpus_slug", ""),
        fetch_strategy=data.get("fetch_strategy", ""),
        source_type=source.get("type", ""),
        source_url=source.get("url", ""),
        source_version=source.get("source_version") or DEFAULT_SOURCE_VERSION,
        resolved_version=source.get("resolved_version"),
        fetched_at=source.get("fetched_at"),
        snapshot_id=snapshot_id,
        url_set_hash=snapshot.get("url_set_hash"),
        content_hash=snapshot.get("content_hash"),
        fetch_config_hash=snapshot.get("fetch_config_hash"),
        aliases=list(data.get("aliases", [])),
        files=files,
        sections=data.get("sections", []),
        raw=data,
    )


def snapshot_manifest_from_downloads(
    *,
    corpus_slug: str,
    fetch_strategy: str,
    source_type: str,
    source_url: str,
    files: list[Any],
    source_version: str = DEFAULT_SOURCE_VERSION,
    resolved_version: str | None = None,
    fetched_at: str | None = None,
    fetch_config: dict[str, Any] | None = None,
    aliases: list[str] | None = None,
    sections: list[dict[str, Any]] | None = None,
    raw: dict[str, Any] | None = None,
) -> SnapshotManifest:
    timestamp = fetched_at or utc_now_iso()
    file_map: dict[str, ManifestFile] = {}
    urls: list[str] = []
    for result in files:
        filename = result["filename"] if isinstance(result, dict) else result.filename
        url = result.get("url", "") if isinstance(result, dict) else result.url
        success = result.get("success", True) if isinstance(result, dict) else result.success
        error = result.get("error") if isinstance(result, dict) else result.error
        content_hash = result.get("content_hash") if isinstance(result, dict) else result.content_hash
        fetched = result.get("fetched_at", timestamp) if isinstance(result, dict) else getattr(result, "fetched_at", None) or timestamp
        extra = {}
        if error is not None:
            extra["error"] = error
        file_map[filename] = ManifestFile(
            filename=filename,
            url=url,
            success=success,
            content_hash=content_hash,
            fetched_at=fetched,
            source_version=source_version,
            resolved_version=resolved_version,
            extra=extra,
        )
        if success and url:
            urls.append(url)

    content_hash = hash_manifest_files(file_map)
    url_set_hash = hash_strings(sorted(set(urls)))
    fetch_config_hash = hash_json(fetch_config or {})
    manifest = SnapshotManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        corpus_slug=corpus_slug,
        fetch_strategy=fetch_strategy,
        source_type=source_type,
        source_url=source_url,
        source_version=source_version,
        resolved_version=resolved_version,
        fetched_at=timestamp,
        url_set_hash=url_set_hash,
        content_hash=content_hash,
        fetch_config_hash=fetch_config_hash,
        aliases=aliases or [DEFAULT_SOURCE_VERSION],
        files=file_map,
        sections=sections or [],
        raw=raw or {},
    )
    return manifest


def write_snapshot_manifest(manifest: SnapshotManifest, output_dir: Path) -> dict[str, Any]:
    data = finalize_snapshot_manifest(manifest)
    (output_dir / "manifest.json").write_text(json.dumps(data, indent=2))
    return data


def finalize_snapshot_manifest(manifest: SnapshotManifest) -> dict[str, Any]:
    snapshot_id = manifest.snapshot_id or build_snapshot_id(
        corpus_slug=manifest.corpus_slug,
        fetch_strategy=manifest.fetch_strategy,
        source_version=manifest.source_version,
        source_url=manifest.source_url,
        fetch_config_hash=manifest.fetch_config_hash,
        url_set_hash=manifest.url_set_hash,
        content_hash=manifest.content_hash,
    )
    data = dict(manifest.raw)
    data.update({
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus_slug": manifest.corpus_slug,
        "fetch_strategy": manifest.fetch_strategy,
        "source": {
            "type": manifest.source_type,
            "url": manifest.source_url,
            "source_version": manifest.source_version,
            "resolved_version": manifest.resolved_version,
            "fetched_at": manifest.fetched_at,
        },
        "snapshot": {
            "snapshot_id": snapshot_id,
            "url_set_hash": manifest.url_set_hash,
            "content_hash": manifest.content_hash,
            "fetch_config_hash": manifest.fetch_config_hash,
        },
        "aliases": manifest.aliases,
        "files": [manifest.files[key].to_entry() for key in sorted(manifest.files)],
    })
    if manifest.sections:
        data["sections"] = manifest.sections
    return data
