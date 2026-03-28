"""Tests for doc_hub.clean — LLM markdown cleaning module.

Unit tests only — no network calls. The OpenAI client is mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.clean import (
    DEFAULT_CLEAN_PROMPT,
    CleanConfig,
    CleanResult,
    clean_corpus,
    clean_markdown,
    get_clean_config,
)


# ---------------------------------------------------------------------------
# get_clean_config
# ---------------------------------------------------------------------------


class TestGetCleanConfig:
    def test_all_vars_set(self, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "sk-test")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "https://api.openai.com/v1")

        cfg = get_clean_config()

        assert cfg.model == "gpt-4o-mini"
        assert cfg.api_key == "sk-test"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.prompt == DEFAULT_CLEAN_PROMPT

    def test_custom_prompt(self, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")
        monkeypatch.setenv("DOC_HUB_CLEAN_PROMPT", "Custom prompt")

        cfg = get_clean_config()
        assert cfg.prompt == "Custom prompt"

    def test_missing_model_raises(self, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")
        monkeypatch.delenv("DOC_HUB_CLEAN_MODEL", raising=False)

        with pytest.raises(ValueError, match="DOC_HUB_CLEAN_MODEL"):
            get_clean_config()

    def test_missing_all_raises(self, monkeypatch):
        monkeypatch.delenv("DOC_HUB_CLEAN_MODEL", raising=False)
        monkeypatch.delenv("DOC_HUB_CLEAN_API_KEY", raising=False)
        monkeypatch.delenv("DOC_HUB_CLEAN_BASE_URL", raising=False)

        with pytest.raises(ValueError, match="DOC_HUB_CLEAN_MODEL.*DOC_HUB_CLEAN_API_KEY.*DOC_HUB_CLEAN_BASE_URL"):
            get_clean_config()


# ---------------------------------------------------------------------------
# clean_markdown
# ---------------------------------------------------------------------------


class TestCleanMarkdown:
    @pytest.mark.asyncio
    async def test_returns_cleaned_content(self):
        config = CleanConfig(
            model="test-model",
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            prompt="Clean this",
        )

        mock_message = MagicMock()
        mock_message.content = "# Clean Title\n\nClean content here."
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch("doc_hub.clean.AsyncOpenAI") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_instance

            result = await clean_markdown("# Dirty\n\nNav stuff\n\nContent", config)

            assert result == "# Clean Title\n\nClean content here."
            MockClient.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1")
            mock_instance.chat.completions.create.assert_called_once()
            call_kwargs = mock_instance.chat.completions.create.call_args[1]
            assert call_kwargs["model"] == "test-model"
            assert call_kwargs["messages"][0]["role"] == "system"
            assert call_kwargs["messages"][0]["content"] == "Clean this"
            assert call_kwargs["messages"][1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_returns_empty_on_none_content(self):
        config = CleanConfig(model="m", api_key="k", base_url="u", prompt="p")

        mock_message = MagicMock()
        mock_message.content = None
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch("doc_hub.clean.AsyncOpenAI") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_instance

            result = await clean_markdown("input", config)
            assert result == ""


# ---------------------------------------------------------------------------
# clean_corpus
# ---------------------------------------------------------------------------


class TestCleanCorpus:
    def _write_manifest(self, output_dir: Path, files: list[dict]) -> None:
        manifest = {"total": len(files), "files": files}
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    @pytest.mark.asyncio
    async def test_skips_already_clean_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")

        # File with matching clean_hash should be skipped
        self._write_manifest(tmp_path, [
            {
                "filename": "already-clean.md",
                "url": "https://example.com/clean",
                "success": True,
                "content_hash": "abc123",
                "clean_hash": "abc123",
            },
        ])
        (tmp_path / "already-clean.md").write_text("Already clean content")

        results = await clean_corpus(tmp_path)
        assert results == []

    @pytest.mark.asyncio
    async def test_cleans_changed_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")

        self._write_manifest(tmp_path, [
            {
                "filename": "needs-clean.md",
                "url": "https://example.com/dirty",
                "success": True,
                "content_hash": "new-hash",
                "clean_hash": "old-hash",
            },
        ])
        (tmp_path / "needs-clean.md").write_text("Dirty content with nav")

        with patch("doc_hub.clean.clean_markdown", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = "Clean content"
            results = await clean_corpus(tmp_path)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].filename == "needs-clean.md"

        # Verify file was updated
        assert (tmp_path / "needs-clean.md").read_text() == "Clean content"

        # Verify manifest was updated with clean_hash
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        entry = manifest["files"][0]
        assert entry["clean_hash"] == "new-hash"

    @pytest.mark.asyncio
    async def test_cleans_files_without_clean_hash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")

        # File with no clean_hash (never cleaned)
        self._write_manifest(tmp_path, [
            {
                "filename": "new-file.md",
                "url": "https://example.com/new",
                "success": True,
                "content_hash": "abc123",
            },
        ])
        (tmp_path / "new-file.md").write_text("Raw content")

        with patch("doc_hub.clean.clean_markdown", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = "Cleaned"
            results = await clean_corpus(tmp_path)

        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_skips_failed_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")

        self._write_manifest(tmp_path, [
            {
                "filename": "failed.md",
                "url": "https://example.com/fail",
                "success": False,
                "content_hash": None,
                "error": "404",
            },
        ])

        results = await clean_corpus(tmp_path)
        assert results == []

    @pytest.mark.asyncio
    async def test_handles_clean_error_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")

        self._write_manifest(tmp_path, [
            {
                "filename": "error-file.md",
                "url": "https://example.com/err",
                "success": True,
                "content_hash": "abc123",
            },
        ])
        (tmp_path / "error-file.md").write_text("Content")

        with patch("doc_hub.clean.clean_markdown", new_callable=AsyncMock) as mock_clean:
            mock_clean.side_effect = Exception("API timeout")
            results = await clean_corpus(tmp_path)

        assert len(results) == 1
        assert results[0].success is False
        assert "API timeout" in results[0].error

        # Verify manifest was NOT updated with clean_hash for failed file
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert "clean_hash" not in manifest["files"][0]

    @pytest.mark.asyncio
    async def test_no_manifest_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")

        results = await clean_corpus(tmp_path)
        assert results == []

    @pytest.mark.asyncio
    async def test_missing_file_on_disk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_CLEAN_MODEL", "m")
        monkeypatch.setenv("DOC_HUB_CLEAN_API_KEY", "k")
        monkeypatch.setenv("DOC_HUB_CLEAN_BASE_URL", "u")

        self._write_manifest(tmp_path, [
            {
                "filename": "ghost.md",
                "url": "https://example.com/ghost",
                "success": True,
                "content_hash": "abc123",
            },
        ])
        # Don't create the file on disk

        results = await clean_corpus(tmp_path)
        assert len(results) == 1
        assert results[0].success is False
        assert "file not found" in results[0].error
