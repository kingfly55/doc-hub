from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_ENV = REPO_ROOT / ".agent" / "install-manager" / "scripts" / "check-env.sh"


def test_check_env_reports_global_env_file_when_present(tmp_path):
    global_env = tmp_path / "doc-hub" / "env"
    global_env.parent.mkdir(parents=True)
    global_env.write_text(
        "\n".join(
            [
                "PGHOST=localhost",
                "PGPORT=5433",
                "PGUSER=postgres",
                "PGPASSWORD=secret",
                "PGDATABASE=postgres",
                "GEMINI_API_KEY=test-key",
                "",
            ]
        )
    )

    result = subprocess.run(
        [str(CHECK_ENV)],
        cwd=REPO_ROOT,
        env={"XDG_DATA_HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "GLOBAL_ENV_FILE=" in result.stdout
    assert str(global_env) in result.stdout
    assert "GLOBAL_ENV_FILE_STATUS=present" in result.stdout
    assert "GLOBAL_ENV_KEYS=GEMINI_API_KEY,PGDATABASE,PGHOST,PGPASSWORD,PGPORT,PGUSER" in result.stdout
