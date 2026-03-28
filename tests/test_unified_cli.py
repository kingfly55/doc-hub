from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def test_unified_cli_importable():
    from doc_hub.cli.main import main

    assert callable(main)


def test_top_level_groups_parse():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["docs", "browse", "demo"])

    assert args.command_group == "docs"
    assert args.docs_command == "browse"
    assert args.corpus == "demo"


def test_docs_list_emits_human_readable_output(capsys):
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())
    corpora = [
        SimpleNamespace(slug="pydantic-ai", name="Pydantic AI", enabled=True),
        SimpleNamespace(slug="legacy", name="Legacy Docs", enabled=False),
    ]

    with patch("doc_hub.cli.docs.create_pool", AsyncMock(return_value=pool)), patch(
        "doc_hub.cli.docs.ensure_schema", AsyncMock()
    ), patch("doc_hub.cli.docs.list_corpora", AsyncMock(return_value=corpora)):
        main(["docs", "list"])

    assert capsys.readouterr().out == (
        "Pydantic AI [pydantic-ai] - enabled\n"
        "Legacy Docs [legacy] - disabled\n"
    )


def test_docs_list_emits_json_output(capsys):
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())
    corpora = [
        SimpleNamespace(slug="pydantic-ai", name="Pydantic AI", enabled=True),
        SimpleNamespace(slug="legacy", name="Legacy Docs", enabled=False),
    ]

    with patch("doc_hub.cli.docs.create_pool", AsyncMock(return_value=pool)), patch(
        "doc_hub.cli.docs.ensure_schema", AsyncMock()
    ), patch("doc_hub.cli.docs.list_corpora", AsyncMock(return_value=corpora)):
        main(["docs", "list", "--json"])

    assert json.loads(capsys.readouterr().out) == [
        {"slug": "pydantic-ai", "display_name": "Pydantic AI", "enabled": True},
        {"slug": "legacy", "display_name": "Legacy Docs", "enabled": False},
    ]


def test_man_prints_bundled_manpage_output(capsys):
    from doc_hub.cli.main import main

    main(["man"])

    output = capsys.readouterr().out
    assert "doc-hub docs list" in output
    assert "List registered corpora." in output


def test_docs_search_routes_to_search_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.docs.handle_search") as mock_handler:
        main(["docs", "search", "--corpus", "pydantic-ai", "retry logic"])

    mock_handler.assert_called_once()


def test_man_routes_to_man_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.main.handle_man") as mock_handler:
        main(["man"])

    mock_handler.assert_called_once()


def test_man_prints_bundled_manpage(capsys):
    from doc_hub.cli.docs import handle_man

    handle_man(argparse.Namespace())

    out = capsys.readouterr().out
    assert "COMMANDS" in out
    assert "doc-hub docs list" in out
    assert "doc-hub man" in out
    assert "doc-hub docs search --corpus pydantic-ai \"retry logic\"" in out
    assert "doc-hub docs read pydantic-ai abc123" in out
    assert "doc-hub serve mcp" in out
    assert "ENVIRONMENT" in out


def test_man_falls_back_to_installed_manpath(capsys, tmp_path):
    from doc_hub.cli import docs as docs_module

    installed_manpage = tmp_path / "share" / "man" / "man1" / "doc-hub.1"
    installed_manpage.parent.mkdir(parents=True)
    installed_manpage.write_text('.TH DOC-HUB 1\n.SH NAME\ndoc-hub - test manpage\n')

    missing_module = tmp_path / "site-packages" / "doc_hub" / "cli" / "docs.py"
    missing_module.parent.mkdir(parents=True)
    missing_module.write_text("# test placeholder\n")

    with (
        patch.object(docs_module, "__file__", str(missing_module)),
        patch.object(docs_module.sys, "prefix", str(tmp_path)),
    ):
        docs_module.handle_man(argparse.Namespace())

    assert "NAME" in capsys.readouterr().out


def test_pipeline_eval_routes_to_eval_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.pipeline.handle_eval") as mock_handler:
        main(["pipeline", "eval", "--all"])

    mock_handler.assert_called_once()


def test_serve_mcp_routes_to_mcp_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.serve.handle_mcp") as mock_handler:
        main(["serve", "mcp", "--transport", "stdio"])

    mock_handler.assert_called_once()


def test_pyproject_packages_doc_hub_manpage():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text()

    assert (root / "man" / "doc-hub.1").exists()
    assert "share/man/man1/doc-hub.1" in pyproject


def test_doc_hub_manpage_renders():
    import subprocess
    from pathlib import Path

    manpage = Path(__file__).resolve().parent.parent / "man" / "doc-hub.1"
    result = subprocess.run(["man", "-l", str(manpage)], capture_output=True, text=True, check=True)

    assert "doc-hub" in result.stdout
    assert "doc-hub docs list" in result.stdout
    assert "doc-hub man" in result.stdout
    assert "doc-hub docs search --corpus CORPUS" in result.stdout


def test_docs_mention_manpage_and_corpus_listing():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text()
    cli_reference = (root / "docs" / "user" / "cli-reference.md").read_text()

    assert "man doc-hub" in readme
    assert "doc-hub docs list" in readme
    assert "man doc-hub" in cli_reference
    assert "doc-hub docs list" in cli_reference


def test_old_script_names_removed_from_pyproject():
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    scripts = data["project"]["scripts"]
    assert scripts == {"doc-hub": "doc_hub.cli.main:main"}


def test_bootstrap_cli_loads_global_env_file_from_xdg_data_home(tmp_path, monkeypatch):
    from doc_hub.cli.shared import bootstrap_cli

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("DOC_HUB_DATA_DIR", raising=False)
    global_env = tmp_path / "xdg" / "doc-hub" / "env"

    with patch("doc_hub.cli.shared.load_dotenv") as mock_load_dotenv, patch(
        "doc_hub.cli.shared.logging.basicConfig"
    ):
        bootstrap_cli()

    assert mock_load_dotenv.call_args_list[0].args == ()
    assert mock_load_dotenv.call_args_list[1].kwargs == {"dotenv_path": global_env}


def test_bootstrap_cli_prefers_doc_hub_data_dir_for_global_env(tmp_path, monkeypatch):
    from doc_hub.cli.shared import bootstrap_cli

    data_dir = tmp_path / "custom-data"
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(data_dir))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    global_env = data_dir / "env"

    with patch("doc_hub.cli.shared.load_dotenv") as mock_load_dotenv, patch(
        "doc_hub.cli.shared.logging.basicConfig"
    ):
        bootstrap_cli()

    assert mock_load_dotenv.call_args_list[1].kwargs == {"dotenv_path": global_env}


def test_slugify_basic_cases():
    from doc_hub.cli.pipeline import slugify

    assert slugify("Pydantic AI") == "pydantic-ai"
    assert slugify("FastAPI") == "fastapi"
    assert slugify("My  Great--Docs") == "my-great-docs"
    assert slugify("  Leading Trailing  ") == "leading-trailing"
    assert slugify("Anthropic SDK (Python)") == "anthropic-sdk-python"


def test_pipeline_add_parses_llms_txt_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "Pydantic AI",
        "--strategy", "llms_txt",
        "--url", "https://ai.pydantic.dev/llms.txt",
    ])

    assert args.command_group == "pipeline"
    assert args.pipeline_command == "add"
    assert args.name == "Pydantic AI"
    assert args.strategy == "llms_txt"
    assert args.url == "https://ai.pydantic.dev/llms.txt"
    assert args.slug is None
    assert args.no_index is False


def test_pipeline_add_parses_local_dir_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "My Docs",
        "--strategy", "local_dir",
        "--path", "/tmp/docs",
        "--slug", "my-docs",
        "--no-index",
    ])

    assert args.name == "My Docs"
    assert args.strategy == "local_dir"
    assert args.path == "/tmp/docs"
    assert args.slug == "my-docs"
    assert args.no_index is True


def test_pipeline_add_parses_git_repo_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "Anthropic SDK",
        "--strategy", "git_repo",
        "--url", "https://github.com/anthropics/anthropic-sdk-python.git",
        "--branch", "main",
        "--docs-dir", "docs",
    ])

    assert args.strategy == "git_repo"
    assert args.url == "https://github.com/anthropics/anthropic-sdk-python.git"
    assert args.branch == "main"
    assert args.docs_dir == "docs"


def test_pipeline_add_parses_sitemap_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "FastAPI",
        "--strategy", "sitemap",
        "--url", "https://fastapi.tiangolo.com/sitemap.xml",
    ])

    assert args.strategy == "sitemap"
    assert args.url == "https://fastapi.tiangolo.com/sitemap.xml"


def test_pipeline_add_builds_config_and_upserts_llms_txt():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("llms_txt", argparse.Namespace(
        url="https://ai.pydantic.dev/llms.txt",
        path=None,
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch=None,
        docs_dir=None,
    ))
    assert config == {"url": "https://ai.pydantic.dev/llms.txt"}


def test_pipeline_add_builds_config_llms_txt_with_optionals():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("llms_txt", argparse.Namespace(
        url="https://ai.pydantic.dev/llms.txt",
        path=None,
        url_pattern=r"https://ai\.pydantic\.dev/[^\s]+\.md",
        base_url="https://ai.pydantic.dev/",
        workers=10,
        retries=5,
        branch=None,
        docs_dir=None,
    ))
    assert config == {
        "url": "https://ai.pydantic.dev/llms.txt",
        "url_pattern": r"https://ai\.pydantic\.dev/[^\s]+\.md",
        "base_url": "https://ai.pydantic.dev/",
        "workers": 10,
        "retries": 5,
    }


def test_pipeline_add_builds_config_local_dir():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("local_dir", argparse.Namespace(
        url=None,
        path="/tmp/docs",
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch=None,
        docs_dir=None,
    ))
    assert config == {"path": "/tmp/docs"}


def test_pipeline_add_builds_config_git_repo():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("git_repo", argparse.Namespace(
        url="https://github.com/org/repo.git",
        path=None,
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch="main",
        docs_dir="docs",
    ))
    assert config == {
        "url": "https://github.com/org/repo.git",
        "branch": "main",
        "docs_dir": "docs",
    }


def test_pipeline_add_builds_config_sitemap():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("sitemap", argparse.Namespace(
        url="https://example.com/sitemap.xml",
        path=None,
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch=None,
        docs_dir=None,
    ))
    assert config == {"url": "https://example.com/sitemap.xml"}


def test_pipeline_add_missing_url_raises():
    from doc_hub.cli.pipeline import build_fetch_config

    try:
        build_fetch_config("llms_txt", argparse.Namespace(
            url=None,
            path=None,
            url_pattern=None,
            base_url=None,
            workers=None,
            retries=None,
            branch=None,
            docs_dir=None,
        ))
        assert False, "Expected SystemExit"
    except SystemExit:
        pass


def test_pipeline_add_missing_path_raises():
    from doc_hub.cli.pipeline import build_fetch_config

    try:
        build_fetch_config("local_dir", argparse.Namespace(
            url=None,
            path=None,
            url_pattern=None,
            base_url=None,
            workers=None,
            retries=None,
            branch=None,
            docs_dir=None,
        ))
        assert False, "Expected SystemExit"
    except SystemExit:
        pass


def test_pipeline_add_registers_and_runs_pipeline():
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.db.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.db.ensure_schema", AsyncMock()),
        patch("doc_hub.db.upsert_corpus", AsyncMock()) as mock_upsert,
        patch("doc_hub.pipeline.run_pipeline", AsyncMock()) as mock_pipeline,
    ):
        main([
            "pipeline", "add", "Pydantic AI",
            "--strategy", "llms_txt",
            "--url", "https://ai.pydantic.dev/llms.txt",
        ])

    mock_upsert.assert_called_once()
    corpus = mock_upsert.call_args[0][1]
    assert corpus.slug == "pydantic-ai"
    assert corpus.name == "Pydantic AI"
    assert corpus.fetch_strategy == "llms_txt"
    assert corpus.fetch_config == {"url": "https://ai.pydantic.dev/llms.txt"}
    mock_pipeline.assert_called_once()


def test_pipeline_add_no_index_skips_pipeline():
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.db.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.db.ensure_schema", AsyncMock()),
        patch("doc_hub.db.upsert_corpus", AsyncMock()),
        patch("doc_hub.pipeline.run_pipeline", AsyncMock()) as mock_pipeline,
    ):
        main([
            "pipeline", "add", "Pydantic AI",
            "--strategy", "llms_txt",
            "--url", "https://ai.pydantic.dev/llms.txt",
            "--no-index",
        ])

    mock_pipeline.assert_not_called()


def test_pipeline_add_custom_slug():
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.db.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.db.ensure_schema", AsyncMock()),
        patch("doc_hub.db.upsert_corpus", AsyncMock()) as mock_upsert,
        patch("doc_hub.pipeline.run_pipeline", AsyncMock()),
    ):
        main([
            "pipeline", "add", "Pydantic AI",
            "--strategy", "llms_txt",
            "--url", "https://ai.pydantic.dev/llms.txt",
            "--slug", "pai",
        ])

    corpus = mock_upsert.call_args[0][1]
    assert corpus.slug == "pai"


def test_pipeline_logs_parses_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["pipeline", "logs", "pydantic-ai"])

    assert args.command_group == "pipeline"
    assert args.pipeline_command == "logs"
    assert args.slug == "pydantic-ai"


def test_pipeline_logs_runs_pipeline_with_logging():
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())
    corpus = SimpleNamespace(
        slug="pydantic-ai",
        name="Pydantic AI",
        fetch_strategy="llms_txt",
        fetch_config={"url": "https://ai.pydantic.dev/llms.txt"},
        parser="markdown",
        embedder="gemini",
        enabled=True,
        last_indexed_at=None,
        total_chunks=42,
    )

    with (
        patch("doc_hub.db.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.db.ensure_schema", AsyncMock()),
        patch("doc_hub.db.get_corpus", AsyncMock(return_value=corpus)),
        patch("doc_hub.pipeline.run_pipeline", AsyncMock()) as mock_pipeline,
    ):
        main(["pipeline", "logs", "pydantic-ai"])

    mock_pipeline.assert_called_once()


def test_pipeline_logs_corpus_not_found(capsys):
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.db.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.db.ensure_schema", AsyncMock()),
        patch("doc_hub.db.get_corpus", AsyncMock(return_value=None)),
    ):
        try:
            main(["pipeline", "logs", "nonexistent"])
            assert False, "Expected SystemExit"
        except SystemExit as e:
            assert e.code == 1

    assert "not found" in capsys.readouterr().err
