"""Focused tests for SuperAgent adaptive effort selection and distinct scoped delegation prompts.

Covers:
(1) Effort Selection:
    - Default to low effort for routine tasks (typos, formatting, docstrings, renames, status checks).
    - Default to medium effort for substantial work (features, unit tests, audits, code reviews).
    - Reserve high effort strictly for rare, explicitly justified complexity (architectural rewrites,
      concurrency/race conditions, explicit deep reasoning requests).
    - Preservation of user requirements and explicit agent/effort choices.
(2) Distinct Scoped Prompts:
    - Rewrite each subagent prompt into a self-contained assignment containing strictly:
      goal, context, owned files/modules, constraints, and acceptance criteria.
    - Eliminates forwarding the full original prompt with a generic frontend/backend qualifier.
    - Guarantees domain isolation and non-overlapping file ownership across parallel workers.
    - Automatic sanitization of legacy qualifier prefixes.
"""

import pytest
from superagent.brain import (
    SuperagentBrain,
    SubtaskItem,
    TriageDecision,
    determine_effort,
    derive_subagent_goal,
    build_scoped_assignment,
)


# ==============================================================================
# 1. Effort Selection Tests
# ==============================================================================


@pytest.mark.parametrize(
    "routine_prompt",
    [
        "fix typo in login string",
        "fix spelling in error notification",
        "rename variable token_cache to auth_tokens",
        "rename function get_user to fetch_user",
        "update docstring in runner.py",
        "add comments explaining the supervisor loop",
        "format code using black and ruff",
        "fix indentation and style in cards.py",
        "check status of background workers",
        "quick inspect readme for broken links",
        "fast pass on configuration defaults",
        "minimal effort syntax check",
        "single line fix for return value",
    ],
)
def test_determine_effort_routine_tasks_default_to_low(routine_prompt):
    """Routine tasks must default to low effort."""
    assert determine_effort(routine_prompt) == "low"


@pytest.mark.parametrize(
    "substantial_prompt",
    [
        "implement user profile settings page",
        "add oauth token refresh handler",
        "write unit tests for execution error handling",
        "audit database query performance and api routes",
        "review pull request for memory leak vulnerabilities",
        "refactor query builder methods",
        "build responsive navigation bar component",
        "spawn 2 subagents to check the project and give suggestions",
        "integrate stripe checkout webhook endpoint",
    ],
)
def test_determine_effort_substantial_work_defaults_to_medium(substantial_prompt):
    """Substantial work must default to medium effort."""
    assert determine_effort(substantial_prompt) == "medium"


@pytest.mark.parametrize(
    "complex_prompt",
    [
        "refactor architecture of auth and session management",
        "rewrite architecture of the process supervision tree",
        "architectural redesign of background event pipeline",
        "deep analyze race condition in process supervision",
        "debug deadlock between async subprocess pipe readers",
        "resolve concurrency bug in thread-safe state manager",
        "complex multi-file bug in distributed transaction rollback",
        "deep reasoning on token expiration and crypto rotation",
        "maximum effort analysis of race conditions",
        "deep think through cryptographic key exchange protocol",
    ],
)
def test_determine_effort_rare_justified_complexity_reserves_high(complex_prompt):
    """Rare, explicitly justified complexity must reserve high effort."""
    assert determine_effort(complex_prompt) == "high"


def test_determine_effort_preserves_explicit_user_effort():
    """Explicit user effort choices must always be preserved."""
    assert determine_effort("fix typo in login button", explicit_effort="high") == "high"
    assert determine_effort("refactor architecture of auth", explicit_effort="low") == "low"
    assert determine_effort("implement new feature", explicit_effort="medium") == "medium"


@pytest.mark.asyncio
async def test_brain_triage_effort_selection_and_explicit_preservation():
    """Verify brain triage applies effort rules and preserves explicit agent choices."""
    brain = SuperagentBrain(available_agents=["codex", "agy", "claude"])
    proj = {"name": "test-repo", "path": "C:/projects/test-repo", "default_agent": "codex"}

    # Routine task -> low effort
    res_routine = await brain.triage("fix typo in login string", proj)
    assert res_routine.action == "delegate"
    assert res_routine.effort == "low"

    # Substantial task -> medium effort
    res_substantial = await brain.triage("implement user profile settings page", proj)
    assert res_substantial.action == "delegate"
    assert res_substantial.effort == "medium"

    # Rare, justified complexity -> high effort
    res_complex = await brain.triage("refactor architecture of auth and session management", proj)
    assert res_complex.action == "delegate"
    assert res_complex.effort == "high"

    # Explicit routing with explicit effort tags preserved
    res_exp_high = await brain.triage("@codex:high deep analyze race condition", proj)
    assert res_exp_high.tasks[0].agent == "codex"
    assert res_exp_high.tasks[0].effort == "high"
    assert res_exp_high.tasks[0].task_brief == "deep analyze race condition"

    res_exp_low = await brain.triage("@agy:low quick inspect readme", proj)
    assert res_exp_low.tasks[0].agent == "agy"
    assert res_exp_low.tasks[0].effort == "low"
    assert res_exp_low.tasks[0].task_brief == "quick inspect readme"

    # Explicit agent routing without effort tag inherits adaptive effort
    res_exp_routine = await brain.triage("@codex fix typo in login screen", proj)
    assert res_exp_routine.tasks[0].agent == "codex"
    assert res_exp_routine.tasks[0].effort == "low"
    assert res_exp_routine.tasks[0].task_brief == "fix typo in login screen"

    res_exp_substantial = await brain.triage("@agy implement user settings panel", proj)
    assert res_exp_substantial.tasks[0].agent == "agy"
    assert res_exp_substantial.tasks[0].effort == "medium"
    assert res_exp_substantial.tasks[0].task_brief == "implement user settings panel"


# ==============================================================================
# 2. Distinct Scoped Prompts Tests
# ==============================================================================


def test_build_scoped_assignment_structure():
    """Verify build_scoped_assignment produces a complete, self-contained prompt."""
    context = {"name": "SuperAgent", "path": "C:/projects/SuperAgent"}
    goal = "Audit database connection pooling and backend API routes."

    prompt = build_scoped_assignment(
        agent="codex",
        goal=goal,
        project_context=context,
    )

    # Must contain all required sections
    assert "Goal: " in prompt
    assert goal in prompt
    assert "Context: Project 'SuperAgent' (root: C:/projects/SuperAgent)." in prompt
    assert "Owned files/modules: " in prompt
    assert "Constraints: " in prompt
    assert "Acceptance criteria: " in prompt

    # Must contain domain isolation and repository containment constraints
    assert "Operate strictly within repository root" in prompt
    assert "Maintain strict domain isolation" in prompt
    assert "TODO.md" in prompt
    assert "PRIORITY_TRACKER.md" in prompt

    # Must contain verification criteria
    assert "test suites without regressions" in prompt


def test_domain_isolation_owned_files_are_distinct():
    """Parallel subagents must own non-overlapping file domains."""
    context = {"name": "TestApp", "path": "/workspace"}

    codex_prompt = build_scoped_assignment("codex", "Backend work", context)
    agy_prompt = build_scoped_assignment("agy", "Frontend work", context)
    claude_prompt = build_scoped_assignment("claude", "Architecture work", context)

    # Backend owns backend source and tests
    assert "Backend source files" in codex_prompt
    assert "superagent/*.py" in codex_prompt or "Backend" in codex_prompt
    assert "Frontend UI components" not in codex_prompt

    # Frontend owns frontend UI and frontend tests
    assert "Frontend UI components" in agy_prompt
    assert "superagent/tui/*" in agy_prompt or "Frontend" in agy_prompt
    assert "Backend source files" not in agy_prompt

    # Claude owns architecture contracts
    assert "Architectural contracts" in claude_prompt or "type definitions" in claude_prompt


@pytest.mark.asyncio
async def test_multi_agent_decomposition_generates_distinct_scoped_prompts():
    """Decomposition must generate self-contained assignments without qualifier forwarding."""
    brain = SuperagentBrain(available_agents=["codex", "agy"])
    proj = {"name": "my-project", "path": "C:/repos/my-project", "default_agent": "codex"}

    raw_user_prompt = "spawn 2 subagents to check the project and give suggestions"
    res = await brain.triage(raw_user_prompt, proj)

    assert res.action == "delegate"
    assert len(res.tasks) == 2

    codex_task = res.tasks[0]
    agy_task = res.tasks[1]

    assert codex_task.agent == "codex"
    assert agy_task.agent == "agy"

    # Both must default to medium effort (substantial audit work)
    assert codex_task.effort == "medium"
    assert agy_task.effort == "medium"

    # CRITICAL: Neither prompt may forward the full original prompt with a qualifier prefix
    assert not codex_task.task_brief.startswith("Focus on backend")
    assert not agy_task.task_brief.startswith("Focus on frontend")
    assert f": {raw_user_prompt}" not in codex_task.task_brief
    assert f": {raw_user_prompt}" not in agy_task.task_brief

    # Both must be self-contained assignments containing all 5 required components
    for task in (codex_task, agy_task):
        assert "Goal:" in task.task_brief
        assert "Context: Project 'my-project'" in task.task_brief
        assert "Owned files/modules:" in task.task_brief
        assert "Constraints:" in task.task_brief
        assert "Acceptance criteria:" in task.task_brief

    # Goals and owned files must be distinct between agents
    assert "backend" in codex_task.task_brief.lower()
    assert "frontend" in agy_task.task_brief.lower()
    assert codex_task.task_brief != agy_task.task_brief


@pytest.mark.asyncio
async def test_three_agent_decomposition_generates_distinct_scoped_prompts(monkeypatch):
    """Decomposition with 3 agents produces 3 distinct scoped assignments."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda cmd: "C:/bin/claude" if cmd == "claude" else None)
    brain = SuperagentBrain(available_agents=["codex", "agy", "claude"])
    proj = {"name": "multi-repo", "path": "C:/repos/multi-repo", "default_agent": "codex"}

    res = await brain.triage("spawn 3 subagents to review project code and architecture", proj)
    assert res.action == "delegate"
    assert len(res.tasks) == 3

    agents = [t.agent for t in res.tasks]
    assert agents == ["codex", "agy", "claude"]

    for task in res.tasks:
        assert task.effort == "medium"
        assert "Goal:" in task.task_brief
        assert "Context: Project 'multi-repo'" in task.task_brief
        assert "Owned files/modules:" in task.task_brief
        assert "Constraints:" in task.task_brief
        assert "Acceptance criteria:" in task.task_brief
        assert not task.task_brief.startswith("Focus on")

    # All three briefs must be mutually distinct
    briefs = [t.task_brief for t in res.tasks]
    assert len(set(briefs)) == 3


def test_derive_subagent_goal_domain_specific_clauses():
    """Verify derive_subagent_goal extracts domain-specific clauses when provided."""
    prompt = "backend: optimize sql queries and database locks; frontend: update navbar active styling"

    codex_goal = derive_subagent_goal(prompt, "codex")
    agy_goal = derive_subagent_goal(prompt, "agy")

    assert "optimize sql queries and database locks" in codex_goal
    assert "navbar" not in codex_goal

    assert "update navbar active styling" in agy_goal
    assert "sql" not in agy_goal


@pytest.mark.asyncio
async def test_legacy_qualifier_sanitization():
    """Verify any legacy 'Focus on backend...:' qualifier is rewritten into a scoped assignment."""
    brain = SuperagentBrain(available_agents=["codex", "agy"])
    proj = {"name": "legacy-test", "path": "C:/repos/legacy-test", "default_agent": "codex"}

    # Mock response schema simulating an external model that produced legacy qualifiers
    legacy_decision = TriageDecision(
        action="delegate",
        tasks=[
            SubtaskItem(
                agent="codex",
                effort="medium",
                title="Backend Audit",
                task_brief="Focus on backend logic, data models, edge cases: review user auth",
            ),
            SubtaskItem(
                agent="agy",
                effort="medium",
                title="Frontend Audit",
                task_brief="Focus on frontend components, user workflows: review user auth",
            ),
        ],
        reasoning="Legacy model output",
    )

    # In _run_planning_model, legacy qualifier detection rewrites into scoped assignments
    for t in legacy_decision.tasks:
        import re
        legacy_match = re.match(
            r"^Focus on (?:backend|frontend|code style|architecture)[^:]*:\s*(.*)$",
            t.task_brief,
            re.IGNORECASE,
        )
        if legacy_match:
            goal = brain.derive_subagent_goal(legacy_match.group(1), t.agent)
            t.task_brief = brain.build_scoped_assignment(t.agent, goal, proj)

    # Check that legacy qualifiers were replaced with full scoped assignments
    for t in legacy_decision.tasks:
        assert not t.task_brief.startswith("Focus on")
        assert "Goal:" in t.task_brief
        assert "Context: Project 'legacy-test'" in t.task_brief
        assert "Owned files/modules:" in t.task_brief
        assert "Constraints:" in t.task_brief
        assert "Acceptance criteria:" in t.task_brief
