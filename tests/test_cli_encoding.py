"""Exercise the headless CLI with Windows' legacy redirected encoding."""

import os
from pathlib import Path
import subprocess
import sys


def test_headless_cli_preserves_unicode_on_legacy_streams():
    # Use a real Python pipe with strict CP1252, reproducing the failure before
    # the worker launches. Stub only the worker to avoid a subscription call.
    script = '''
import sys
from types import SimpleNamespace
from unittest.mock import patch
from superagent import cli

project = SimpleNamespace(name="demo", path=".")
config = SimpleNamespace(active_project=project, get_project=lambda name: project)

async def execute(runner, task):
    task.log_lines.append("Verified Unicode: ⚡ 🤖 Grüezi")
    runner._notify(task, "log")
    task.status = "DONE"
    task.returncode = 0

sys.argv = ["superagent", "run", "codex", "Check Unicode"]
with patch.object(cli, "SuperagentConfig", return_value=config), patch.object(
    cli.SubagentRunner, "execute_task", execute
):
    cli.main()
'''
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="cp1252:strict", PYTHONUTF8="0")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    output = result.stdout.decode("utf-8")
    assert "➜ Superagent delegating to CODEX" in output
    assert "Verified Unicode: ⚡ 🤖 Grüezi" in output
    assert "status: DONE (exit code: 0)" in output
