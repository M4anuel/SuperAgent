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
    (tmp_path / 'another').mkdir()
    cfg.add_project("another-repo", str(tmp_path / "another"))
    assert cfg.set_active_project("test-repo") is True
    assert cfg.active_project.name == "test-repo"

    # Agent enabled/disabled
    cfg._data["agents"] = {
        "codex": {"enabled": True},
        "claude": {"enabled": False},
        "agy": {"enabled": True}
    }
    assert "codex" in cfg.get_enabled_agents()
    assert "claude" not in cfg.get_enabled_agents()
    cfg.set_agent_enabled("claude", True)
    assert "claude" in cfg.get_enabled_agents()

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

    # 6. disabled agent routing
    brain.set_available_agents(["codex", "agy"])
    res_disabled = await brain.triage("@claude review something", proj)
    assert res_disabled.action == "reply"
    assert "disabled" in res_disabled.reply_text.lower()

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
    brain = SuperagentBrain(available_agents=[])
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
    assert "CODEX" in synthesis
    assert "AGY" in synthesis
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

@pytest.mark.asyncio
async def test_dynamic_planning_decomposition():
    brain = SuperagentBrain()
    proj = {"name": "testproj", "path": ".", "default_agent": "codex"}

    # 1. Tier 1 Fast Triage returns None for complex task
    complex_prompt = "audit the entire application architecture and suggest improvements"
    fast_res = brain._fast_triage(complex_prompt, proj)
    assert fast_res is None

    # 2. Fast Triage handles conversational and minor atomic immediately
    conv = brain._fast_triage("ping", proj)
    assert conv is not None and conv.action == "reply"

    minor = brain._fast_triage("fix typo in login button", proj)
    assert minor is not None and minor.action == "delegate"
    assert minor.effort == "low"

    # 3. Simulate Planning Model output parsing with markdown formatting
    simulated_output = (
        "PLAN: Coordinate a complementary two-agent audit across backend and frontend.\n"
        "**DELEGATE:** codex | effort: high | Deep audit of session management and SQLite locks\n"
        "- DELEGATE: `agy` | effort: medium | Verify React component responsive layouts and dark mode\n"
    )

    lines = [l.strip() for l in simulated_output.splitlines() if l.strip()]
    plan_line = next((l.split("PLAN:", 1)[1].strip() for l in lines if "PLAN:" in l), "")
    delegate_lines = [l for l in lines if "DELEGATE:" in l]

    tasks = []
    seen = set()
    for d_line in delegate_lines:
        parts = d_line.split("DELEGATE:", 1)[1].split("|")
        agent = parts[0].replace("*", "").replace("`", "").strip().lower()
        effort = "medium"
        if len(parts) > 1 and "effort:" in parts[1]:
            effort = parts[1].replace("effort:", "").replace("*", "").replace("`", "").strip().lower()
        brief = parts[2].replace("*", "").replace("`", "").strip() if len(parts) > 2 else ""
        if brief and brief not in seen:
            seen.add(brief)
            tasks.append((agent, effort, brief))

    assert len(tasks) == 2
    assert tasks[0] == ("codex", "high", "Deep audit of session management and SQLite locks")
    assert tasks[1] == ("agy", "medium", "Verify React component responsive layouts and dark mode")

def test_conversation_grouping_and_resuming(tmp_path):
    from superagent.history import HistoryManager, HistoryItem, Conversation
    from superagent.config import SuperagentConfig

    hm = HistoryManager(base_dir=tmp_path)
    
    # 1. Create a conversation
    conv = hm.create_conversation("myproj", str(tmp_path), title="Initial Chat")
    assert conv.title == "Initial Chat"
    assert conv.project_name == "myproj"
    assert conv.turn_count == 0

    # 2. Add turn
    turn1 = HistoryItem(
        id="turn_1",
        project_name="myproj",
        project_path=str(tmp_path),
        user_prompt="Add dark mode support",
        plan_action="reply",
        direct_reply="Dark mode enabled."
    )
    conv = hm.add_turn_to_conversation(conv.id, turn1)
    assert conv.turn_count == 1
    assert "Add dark mode" in conv.last_snippet

    # 3. Create second conversation with auto-title from first turn
    conv2 = hm.create_conversation("myproj", str(tmp_path))
    turn2 = HistoryItem(
        id="turn_2",
        project_name="myproj",
        project_path=str(tmp_path),
        user_prompt="Fix database deadlock in sqlite wrapper",
        plan_action="reply",
        direct_reply="Fixed."
    )
    conv2 = hm.add_turn_to_conversation(conv2.id, turn2)
    assert conv2.title.startswith("Fix database deadlock")
    assert conv2.turn_count == 1

    # 4. List conversations
    convs = hm.list_conversations("myproj")
    assert len(convs) == 2

    # 5. Config last_conversation tracking
    cfg = SuperagentConfig()
    cfg.config_dir = tmp_path
    cfg.config_file = tmp_path / "config.json"
    cfg._data = {"projects": {}, "active_project": "myproj", "last_conversations": {}}

    cfg.set_last_conversation_id("myproj", conv.id)
    assert cfg.get_last_conversation_id("myproj") == conv.id

def test_task_clean_output_and_ansi():
    rec = TaskRecord(
        task_id="t123",
        agent="codex",
        task_desc="Run audit",
        project_name="proj",
        cwd=".",
        cmd=["codex"]
    )
    rec.log_lines = [
        "OpenAI Codex v0.1.0",
        "workdir: C:\\test",
        "\x1b[32muser\x1b[0m",
        "\x1b[33mcodex\x1b[0m",
        "### Key Findings",
        "1. Found P1 bug in session isolation",
        "2. Found P2 issue in calendar locking",
        "tokens used: 12345"
    ]
    clean = rec.clean_output
    assert "### Key Findings" in clean
    assert "1. Found P1 bug in session isolation" in clean
    assert "2. Found P2 issue in calendar locking" in clean
    assert "OpenAI Codex" not in clean
    assert "tokens used" not in clean
    assert "user" not in clean

@pytest.mark.asyncio
async def test_structured_synthesis_fallback_preserves_findings():
    brain = SuperagentBrain(api_key=None, first_responder="codex", available_agents=[])
    rec1 = TaskRecord("t1", "codex", "Backend audit", "p", ".", ["codex"], effort="high")
    rec1.status = "DONE"
    rec1.log_lines = [f"Improvement #{i}: file_{i}.py detail" for i in range(1, 11)]

    rec2 = TaskRecord("t2", "agy", "Frontend audit", "p", ".", ["agy"], effort="high")
    rec2.status = "DONE"
    rec2.log_lines = [f"UI Suggestion #{i}: component_{i}.tsx detail" for i in range(1, 11)]

    # Mock synthesize_results without CLI or API
    synth = await brain.synthesize_results(
        user_prompt="Find 10 improvements",
        plan_reasoning="2 subagents",
        task_records=[rec1, rec2],
        project_context={"name": "p", "path": "."}
    )

    # Must preserve substantive findings without 280-char truncation
    assert "CODEX" in synth
    assert "AGY" in synth
    assert "Improvement #1" in synth
    assert "Improvement #10" in synth
    assert "UI Suggestion #1" in synth
    assert "UI Suggestion #10" in synth



