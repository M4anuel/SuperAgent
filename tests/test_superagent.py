import os
import time
from pathlib import Path
from superagent.config import SuperagentConfig
from superagent.runner import SubagentRunner, TaskRecord
from superagent.git_utils import GitManager

def test_config_registry(tmp_path):
    cfg = SuperagentConfig()
    cfg.config_dir = tmp_path
    cfg.config_file = tmp_path / "config.json"
    cfg._data = {"projects": {}, "active_project": None}
    
    # Add project
    p = cfg.add_project("test-repo", str(tmp_path), test_cmd="pytest", default_agent="claude")
    assert p.name == "test-repo"
    assert cfg.active_project.name == "test-repo"
    
    # List projects
    projs = cfg.list_projects()
    assert len(projs) == 1
    assert projs[0].name == "test-repo"
    
    # Switch active
    cfg.add_project("another-repo", str(tmp_path / "another"))
    assert cfg.set_active_project("test-repo") is True
    assert cfg.active_project.name == "test-repo"

def test_task_record_timer():
    record = TaskRecord(
        task_id="test1",
        agent="claude",
        task_desc="Run tests",
        project_name="myproj",
        cwd=".",
        cmd=["echo", "hi"]
    )
    assert record.elapsed_seconds == 0
    record.start_time = time.time() - 125  # 2m 5s ago
    assert record.elapsed_seconds >= 125
    assert "02m:" in record.elapsed_formatted

def test_git_manager_basic():
    mgr = GitManager(".")
    assert mgr.is_git_repo() is True
    branch = mgr.current_branch()
    assert len(branch) > 0

import pytest
from superagent.brain import SuperagentBrain

@pytest.mark.asyncio
async def test_brain_triage():
    brain = SuperagentBrain()
    proj = {"name": "test", "path": ".", "default_agent": "codex"}

    # 1. conversational prompt
    res1 = await brain.triage("respond yes", proj)
    assert res1.action == "reply"
    assert res1.reply_text is not None

    # 2. coding task with effort detection
    res2 = await brain.triage("fix typo in login string", proj)
    assert res2.action == "delegate"
    assert res2.effort in ("low", "medium", "high")

    # 3. complex task
    res3 = await brain.triage("refactor architecture of auth and session management", proj)
    assert res3.action == "delegate"
    assert res3.effort == "high"

    # 4. multi-agent decomposition
    res4 = await brain.triage("spawn 2 subagents to check the project and give suggestions", proj)
    assert res4.action == "delegate"
    assert len(res4.tasks) == 2
    assert res4.tasks[0].agent == "codex"
    assert res4.tasks[1].agent == "agy"

    # 5. explicit routing with effort tag
    res5 = await brain.triage("@codex:high deep analyze race condition", proj)
    assert res5.action == "delegate"
    assert len(res5.tasks) == 1
    assert res5.tasks[0].agent == "codex"
    assert res5.tasks[0].effort == "high"

    res6 = await brain.triage("@agy:low quick inspect readme", proj)
    assert res6.action == "delegate"
    assert len(res6.tasks) == 1
    assert res6.tasks[0].agent == "agy"
    assert res6.tasks[0].effort == "low"

def test_runner_agents_md_injection(tmp_path):
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("# Test Rules", encoding="utf-8")
    
    runner = SubagentRunner()
    task = runner.create_task(
        agent="codex",
        task_desc="Run security audit",
        project_name="myproj",
        cwd=str(tmp_path),
        effort="high"
    )
    assert task.task_desc == "Run security audit"
    assert any("[Follow AGENTS.md in repo]" in arg for arg in task.cmd)
    assert '-c' in task.cmd
    assert 'model_reasoning_effort="high"' in task.cmd
