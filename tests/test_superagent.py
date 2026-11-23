import asyncio
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

def test_task_clean_output():
    task = TaskRecord(
        task_id="t123",
        agent="codex",
        task_desc="Analyze models",
        project_name="proj",
        cwd=".",
        cmd=["codex", "exec", "test"]
    )
    task.log_lines = [
        "OpenAI Codex v0.165.0",
        "workdir: C:\\test",
        "model: gpt-6.1-sol",
        "tokens used",
        "3,312",
        "Found 2 potential race conditions in session_manager.py.",
        "Recommended adding async locks around state mutation."
    ]
    clean = task.clean_output
    assert "OpenAI Codex" not in clean
    assert "tokens used" not in clean
    assert "Found 2 potential race conditions in session_manager.py." in clean
    assert "Recommended adding async locks around state mutation." in clean

from superagent.history import HistoryManager, HistoryItem, SubtaskRecordData

def test_history_persistence(tmp_path):
    hm = HistoryManager(base_dir=tmp_path)
    item = HistoryItem(
        id="turn_test_123",
        project_name="alpha",
        project_path=str(tmp_path),
        user_prompt="check auth logic",
        plan_action="delegate",
        plan_reasoning="Decomposed into backend and frontend reviews",
        subtasks=[
            SubtaskRecordData(
                task_id="sub_1",
                agent="codex",
                status="DONE",
                task_brief="Review backend auth",
                output_clean="All tokens properly verified."
            )
        ],
        final_synthesis="Auth review passed cleanly."
    )
    hm.save_turn(item)
    
    # Retrieve
    loaded = hm.get_turn("turn_test_123")
    assert loaded is not None
    assert loaded.user_prompt == "check auth logic"
    assert len(loaded.subtasks) == 1
    assert loaded.subtasks[0].agent == "codex"
    assert loaded.final_synthesis == "Auth review passed cleanly."

    # List
    items = hm.list_turns(project_name="alpha")
    assert len(items) == 1
    assert items[0].id == "turn_test_123"

    # Filter other project
    assert len(hm.list_turns(project_name="beta")) == 0

@pytest.mark.asyncio
async def test_brain_synthesis():
    brain = SuperagentBrain()
    task1 = TaskRecord("t1", "codex", "Audit backend", "p", ".", ["cmd"])
    task1.status = "DONE"
    task1.log_lines = ["Verified 15 endpoints.", "Added 2 missing input validations."]
    task1.diff_summary = "3 files changed, 25 insertions(+)"

    task2 = TaskRecord("t2", "agy", "Audit frontend", "p", ".", ["cmd"])
    task2.status = "DONE"
    task2.log_lines = ["Checked all responsive modals.", "No layout overflow found."]

    synthesis = await brain.synthesize_results(
        user_prompt="audit full application",
        plan_reasoning="split into backend and frontend audit",
        task_records=[task1, task2],
        project_context={"name": "p", "path": "."}
    )
    assert "Fleet Execution: 2/2" in synthesis
    assert "[CODEX]" in synthesis
    assert "[AGY]" in synthesis
    assert "Repository changes detected" in synthesis

@pytest.mark.asyncio
async def test_markup_crash_resilience():
    from superagent.tui.app import SuperagentApp
    app = SuperagentApp()
    async with app.run_test() as pilot:
        task = app.runner.create_task(
            agent="codex",
            task_desc="Review [bracketed code] test",
            project_name="testproj",
            cwd=".",
            effort="medium"
        )
        task.log_lines = [
            ' 300   hlog.write(f"    \\x07 [{s.agent.upper()} ({s.effort})]"',
            'for item in [1, 2, 3]: print(item[0])',
            '[unclosed markup tag',
            'def foo(x: list[int]) -> dict[str, Any]:'
        ]
        
        # Test 1: select_task with raw code lines containing square brackets
        app.select_task(task.task_id)

        # Test 2: live log stream with square brackets
        app.runner._notify(task, "log")

        # Test 3: user prompt containing square brackets
        input_widget = app.query_one("#prompt-input")
        input_widget.value = "check [bracketed input] [0] and [foo]"
        await pilot.press("enter")
        await asyncio.sleep(0.2)
        
        # Verify app is still running and did not crash
        assert app.is_running is True
