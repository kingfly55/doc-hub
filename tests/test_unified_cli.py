from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


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


def test_docs_search_routes_to_search_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.docs.handle_search") as mock_handler:
        main(["docs", "search", "retry logic"])

    mock_handler.assert_called_once()


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
