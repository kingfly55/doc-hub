from __future__ import annotations

import json

from doc_hub.versions import (
    LEGACY_MANIFEST_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    DocVersion,
    ManifestFile,
    SnapshotManifest,
    VersionAlias,
    build_snapshot_id,
    finalize_snapshot_manifest,
    load_snapshot_manifest,
)


def test_build_snapshot_id_is_deterministic():
    first = build_snapshot_id(
        corpus_slug="react",
        fetch_strategy="sitemap",
        source_version="latest",
        source_url="https://react.dev/",
        fetch_config_hash="sha256:config",
        url_set_hash="sha256:urls",
        content_hash="sha256:content",
    )
    second = build_snapshot_id(
        corpus_slug="react",
        fetch_strategy="sitemap",
        source_version="latest",
        source_url="https://react.dev/",
        fetch_config_hash="sha256:config",
        url_set_hash="sha256:urls",
        content_hash="sha256:content",
    )

    assert first == second
    assert first.startswith("sha256-")


def test_build_snapshot_id_changes_when_content_changes():
    first = build_snapshot_id(
        corpus_slug="react",
        fetch_strategy="sitemap",
        source_version="latest",
        source_url="https://react.dev/",
        fetch_config_hash="sha256:config",
        url_set_hash="sha256:urls",
        content_hash="sha256:old",
    )
    second = build_snapshot_id(
        corpus_slug="react",
        fetch_strategy="sitemap",
        source_version="latest",
        source_url="https://react.dev/",
        fetch_config_hash="sha256:config",
        url_set_hash="sha256:urls",
        content_hash="sha256:new",
    )

    assert first != second


def test_load_snapshot_manifest_reads_legacy_manifest(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "files": [
            {
                "filename": "index.md",
                "url": "https://example.com/",
                "success": True,
                "content_hash": "abc123",
            },
            {
                "filename": "missing.md",
                "url": "https://example.com/missing",
                "success": False,
                "error": "404",
            },
        ],
        "custom": "preserved",
    }))

    manifest = load_snapshot_manifest(tmp_path)

    assert manifest.schema_version == LEGACY_MANIFEST_SCHEMA_VERSION
    assert manifest.snapshot_id == ""
    assert manifest.source_version == "latest"
    assert manifest.files == {
        "index.md": ManifestFile(
            filename="index.md",
            url="https://example.com/",
            success=True,
            content_hash="abc123",
        )
    }
    assert manifest.raw["custom"] == "preserved"


def test_load_snapshot_manifest_reads_schema_v2(tmp_path):
    data = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus_slug": "react",
        "fetch_strategy": "sitemap",
        "source": {
            "type": "website",
            "url": "https://react.dev/",
            "source_version": "latest",
            "resolved_version": None,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
        "snapshot": {
            "snapshot_id": "sha256-abc",
            "url_set_hash": "sha256:urls",
            "content_hash": "sha256:content",
            "fetch_config_hash": "sha256:config",
        },
        "aliases": ["latest"],
        "files": [
            {
                "filename": "learn.md",
                "url": "https://react.dev/learn",
                "success": True,
                "content_hash": "filehash",
                "fetched_at": "2026-04-24T12:00:00Z",
                "source_version": "latest",
                "resolved_version": None,
                "extra": "kept",
            }
        ],
        "extra_top_level": True,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(data))

    manifest = load_snapshot_manifest(tmp_path)

    assert manifest.schema_version == MANIFEST_SCHEMA_VERSION
    assert manifest.corpus_slug == "react"
    assert manifest.fetch_strategy == "sitemap"
    assert manifest.source_type == "website"
    assert manifest.source_url == "https://react.dev/"
    assert manifest.source_version == "latest"
    assert manifest.snapshot_id == "sha256-abc"
    assert manifest.aliases == ["latest"]
    assert manifest.files["learn.md"].extra == {"extra": "kept"}
    assert manifest.raw["extra_top_level"] is True


def test_finalize_snapshot_manifest_preserves_unknown_fields():
    manifest = SnapshotManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        corpus_slug="react",
        fetch_strategy="sitemap",
        source_type="website",
        source_url="https://react.dev/",
        source_version="latest",
        fetched_at="2026-04-24T12:00:00Z",
        fetch_config_hash="sha256:config",
        url_set_hash="sha256:urls",
        content_hash="sha256:content",
        aliases=["latest"],
        files={
            "index.md": ManifestFile(
                filename="index.md",
                url="https://react.dev/",
                success=True,
                content_hash="filehash",
                extra={"custom": "value"},
            )
        },
        raw={"extra_top_level": True},
    )

    finalized = finalize_snapshot_manifest(manifest)

    assert finalized["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert finalized["extra_top_level"] is True
    assert finalized["snapshot"]["snapshot_id"].startswith("sha256-")
    assert finalized["files"][0]["custom"] == "value"


def test_version_dataclasses_are_constructible():
    version = DocVersion(
        corpus_id="react",
        snapshot_id="sha256-abc",
        source_version="18",
        source_type="website",
        source_url="https://react.dev/",
        fetch_strategy="sitemap",
        fetch_config_hash="sha256:config",
        content_hash="sha256:content",
        fetched_at="2026-04-24T12:00:00Z",
    )
    alias = VersionAlias(corpus_id="react", alias="latest", snapshot_id="sha256-abc")

    assert version.corpus_id == "react"
    assert alias.alias == "latest"
