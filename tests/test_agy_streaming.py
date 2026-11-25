import pytest
from superagent.execution import agent_command
from superagent.runner import SubagentRunner, TaskRecord

def test_agy_command_includes_stream_json(tmp_path):
    cmd = agent_command('agy', 'inspect files', str(tmp_path), effort='medium')
    assert '--output-format' in cmd
    assert cmd[cmd.index('--output-format') + 1] == 'stream-json'
    assert '-p' in cmd

def test_subagent_runner_parses_agy_stream_json(tmp_path):
    runner = SubagentRunner()
    task = runner.create_task('agy', 'test task', 'proj', str(tmp_path))

    # Test init event
    runner._append_log_line(task, '{"event": "init", "conversation_id": "abcd1234-5678"}')
    assert any("Session initialized" in line for line in task.log_lines)

    # Test step_update with tool call
    runner._append_log_line(task, '{"event": "step_update", "step_update": {"tool_name": "read_file", "tool_info": {"parameters": {"Path": "foo.py"}}}}')
    assert any("🛠️ read_file → foo.py" in line for line in task.log_lines)

    # Test step_update with text delta
    runner._append_log_line(task, '{"event": "step_update", "step_update": {"text_delta": "Thinking step 1\\nRefining logic"}}')
    assert any("Thinking step 1" in line for line in task.log_lines)
    assert any("Refining logic" in line for line in task.log_lines)

    # Test result event
    runner._append_log_line(task, '{"event": "result", "result": {"status": "SUCCESS", "response": "Completed successfully in 2 turns."}}')
    assert any("Task finished (SUCCESS)" in line for line in task.log_lines)
    assert task.clean_output == "Completed successfully in 2 turns."

def test_subagent_runner_preserves_plain_text_logs(tmp_path):
    runner = SubagentRunner()
    task = runner.create_task('codex', 'test task', 'proj', str(tmp_path))

    runner._append_log_line(task, "Plain log line from codex")
    assert task.log_lines[-1] == "Plain log line from codex"
    assert task.clean_output == "Plain log line from codex"

def test_natural_language_agent_routing(tmp_path):
    from superagent.brain import SuperagentBrain
    brain = SuperagentBrain()
    brain.available_agents = ["codex", "agy", "claude"]
    ctx = {"name": "test", "path": str(tmp_path), "default_agent": "codex"}

    decision = brain._local_heuristic_triage("codex failed because it's out of tokens, use agy to overtake the task", ctx)
    assert decision.action == "delegate"
    assert len(decision.tasks) == 1
    assert decision.tasks[0].agent == "agy"

    decision2 = brain._local_heuristic_triage("overtake with claude for the architecture review", ctx)
    assert decision2.action == "delegate"
    assert len(decision2.tasks) == 1
    assert decision2.tasks[0].agent == "claude"

def test_extract_text_from_stream_json():
    from superagent.brain import _extract_text_from_cli_output, SuperagentBrain
    ndjson = (
        '{"type":"init","session_id":"123"}\n'
        '{"type":"step_update","delta":"thinking..."}\n'
        '{"type":"result","result":"PLAN: Test plan\\nDELEGATE: agy | effort: low | Audit files"}'
    )
    extracted = _extract_text_from_cli_output(ndjson)
    assert "PLAN: Test plan" in extracted

    brain = SuperagentBrain()
    brain.available_agents = ["agy", "codex"]
    plan = brain._parse_planner_output(ndjson)
    assert plan.action == "delegate"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].agent == "agy"
    assert plan.tasks[0].effort == "low"
    assert plan.tasks[0].task_brief == "Audit files"
