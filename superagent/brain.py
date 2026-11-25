from __future__ import annotations
import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from pydantic import BaseModel, Field
from superagent.execution import agent_command, validate_project_path
from superagent.processes import ProcessSupervisor

try:
    from google import genai
    from google.genai import types
    HAVE_GEMINI = True
except ImportError:
    HAVE_GEMINI = False

def _extract_text_from_cli_output(output: str) -> str:
    """Extracts clean text/markdown from either plain CLI output or NDJSON stream-json."""
    if not output:
        return ""
    if "{" in output and "}" in output:
        for line in output.splitlines():
            line_str = line.strip()
            if line_str.startswith("{") and line_str.endswith("}"):
                try:
                    event = json.loads(line_str)
                    if event.get("type") == "result" and "result" in event:
                        return str(event["result"]).strip()
                except Exception:
                    pass
    return output.strip()

class SubtaskItem(BaseModel):
    agent: str = Field(default="codex", description="'codex', 'agy', or 'claude'")
    effort: str = Field(default="medium", description="'low', 'medium', or 'high'")
    title: str = Field(default="", description="Short label for this subtask")
    task_brief: str = Field(default="", description="Self-contained actionable instruction for this subagent")

class TriageDecision(BaseModel):
    action: str = Field(
        ...,
        description="'reply' if conversational/question needing direct answer, 'delegate' if coding/analysis tasks required."
    )
    reply_text: Optional[str] = Field(
        default=None,
        description="The direct answer to display to the user if action == 'reply'."
    )
    tasks: List[SubtaskItem] = Field(
        default_factory=list,
        description="List of one or more subagent tasks to execute in parallel."
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of the plan and why these agents/effort levels were chosen."
    )

    # Backward compatibility properties
    @property
    def agent(self) -> str:
        return self.tasks[0].agent if self.tasks else "codex"

    @property
    def effort(self) -> str:
        return self.tasks[0].effort if self.tasks else "medium"

    @property
    def task_brief(self) -> str:
        return self.tasks[0].task_brief if self.tasks else ""

SYSTEM_TRIAGE_INSTRUCTION = """You are SuperAgent, the intelligent coordinator and First Responder for a fleet of subscription-backed coding CLIs:
- `codex`: OpenAI Codex CLI (ChatGPT Plus). Best for deep Python, backend logic, refactors, algorithms, test runs.
- `agy`: Google Antigravity CLI (Google AI Pro). Best for full-stack exploration, frontend/TypeScript, file auditing, fast passes.
- `claude`: Anthropic Claude Code CLI.

Your responsibilities:
1. Direct Conversation (action='reply'):
   If the prompt is conversational, an explanation, a question about the project, or a confirmation (e.g. "respond yes", "hello", "what is this?"):
   - Set action = "reply" and supply the direct response in reply_text. Do NOT delegate.

2. Delegation (action='delegate'):
   If the prompt asks for code changes, bug fixes, testing, or auditing:
   - If the user asks for multiple agents (e.g. "spawn 2 subagents to review..."), create MULTIPLE tasks in the `tasks` array!
     For example:
     * Task 1 (codex, medium): Review backend & database logic, provide suggestions.
     * Task 2 (agy, medium): Review frontend & test suite, provide suggestions.
   - For each task, choose:
     * `agent`: 'codex', 'agy', or 'claude'.
     * `effort`:
       - 'low': Default for routine tasks (status checks, typos, formatting, docstrings, variable renames, trivial syntax fixes).
       - 'medium': Default for substantial work (feature implementations, unit tests, code reviews, audits, bug fixes).
       - 'high': Reserve strictly for rare, explicitly justified complexity (deep architectural rewrites, complex concurrency/race conditions, or explicit user requests for high reasoning).
     * `title`: short 2-4 word summary.
     * `task_brief`: Self-contained assignment containing ONLY its relevant goal, context, owned files/modules, constraints, and acceptance criteria.
       DO NOT forward the full original prompt with a generic frontend/backend qualifier (e.g. never do 'Focus on backend: <original prompt>').
   - SMART TASK SLICING & DOMAIN ISOLATION:
     * If user gives multiple tasks (e.g. 4 tasks across 2 agents), prepare a clear plan checklist.
     * Give each parallel subagent strictly non-overlapping files/modules (e.g. backend vs frontend).
     * Never command parallel agents to edit the same shared tracker or file at the same time.
     * Keep tasks atomic so workers finish in 2-4 minutes without timeouts.
   - Set `reasoning`: 1 sentence explaining the multi-agent plan.
"""

def determine_effort(prompt: str, explicit_effort: Optional[str] = None) -> str:
    """Determine adaptive reasoning effort based on task complexity.

    Rules:
    - Preserves user explicit effort if specified ('low', 'medium', 'high').
    - Defaults to 'low' for routine tasks (syntax/typos/docs/formatting/status checks/renames).
    - Defaults to 'medium' for substantial work (features/implementations/unit tests/audits/code reviews).
    - Reserves 'high' strictly for rare, explicitly justified complexity (architectural rewrites,
      concurrency/race conditions, or explicit user requests for deep reasoning).
    """
    if explicit_effort and explicit_effort.lower() in ("low", "medium", "high"):
        return explicit_effort.lower()

    lower = prompt.lower().strip()

    # 1. Rare, explicitly justified complexity -> high
    explicit_high_phrases = [
        "high effort", "high reasoning", "deep reasoning", "deep think",
        "deep thinking", "maximum effort", "max reasoning", "thorough reasoning",
        "deeply analyze", "deep analysis", "exhaustively analyze"
    ]
    if any(p in lower for p in explicit_high_phrases):
        return "high"

    justified_complexity_patterns = [
        r"\b(?:refactor|rewrite|redesign)\s+(?:the\s+)?architecture\b",
        r"\barchitectur(?:al)?\s+(?:rewrite|refactor|redesign)\b",
        r"\brace\s+condition\b",
        r"\bconcurrency(?:\s+bug|\s+issue|\s+problem|\s+handling|\s+safety)?\b",
        r"\bdeadlock\b",
        r"\bthread\s+safety\b",
        r"\bdistributed\s+consensus\b",
        r"\bcomplex\s+multi-file\s+bug\b",
    ]
    for pattern in justified_complexity_patterns:
        if re.search(pattern, lower):
            return "high"

    # 2. Routine tasks -> low
    routine_phrases = [
        "low effort", "fast pass", "quick pass", "minimal effort",
        "typo", "spelling", "fix spelling",
        "rename variable", "rename function", "rename parameter", "variable rename",
        "docstring", "docstrings", "comment", "comments", "add comments", "documentation",
        "formatting", "format code", "style fix", "lint", "pep8", "whitespace", "indentation",
        "status check", "check status", "quick inspect", "quick check", "inspect readme",
        "single line", "single variable", "minor edit", "trivial"
    ]
    if any(p in lower for p in routine_phrases):
        return "low"

    # 3. Default for substantial work -> medium
    return "medium"

def derive_subagent_goal(prompt: str, agent: str) -> str:
    """Extract or formulate a self-contained goal relevant exclusively to the agent's domain."""
    lower = prompt.lower().strip()

    # Domain-specific clause extraction if explicitly divided in prompt
    backend_match = re.search(r"\bbackend[:\s]+([^;\n]+)", prompt, re.IGNORECASE)
    frontend_match = re.search(r"\bfrontend[:\s]+([^;\n]+)", prompt, re.IGNORECASE)

    if agent == "codex" and backend_match:
        return f"Review and implement backend requirements: {backend_match.group(1).strip()}."
    if agent in ("agy", "antigravity") and frontend_match:
        return f"Review and implement frontend requirements: {frontend_match.group(1).strip()}."

    # Identify user task context/topics
    topics = []
    if "priorit" in lower:
        topics.append("priorities")
    if "auth" in lower or "login" in lower:
        topics.append("authentication and session handling")
    if "test" in lower or "coverage" in lower:
        topics.append("test coverage")
    if "security" in lower:
        topics.append("security hardening")
    if "perf" in lower or "speed" in lower or "bottleneck" in lower:
        topics.append("performance optimization")
    if "refactor" in lower:
        topics.append("refactoring")

    topic_desc = f" focusing on {', '.join(topics)}" if topics else ""

    if agent == "codex":
        return f"Audit and implement backend logic, data models, API services, and backend test suites{topic_desc}."
    elif agent in ("agy", "antigravity"):
        return f"Audit and implement frontend UI components, views, responsive layouts, and user workflows{topic_desc}."
    elif agent == "claude":
        return f"Audit and refactor architectural patterns, type contracts, and cross-cutting maintainability{topic_desc}."
    else:
        return f"Execute assigned domain tasks{topic_desc}."

def build_scoped_assignment(
    agent: str,
    goal: str,
    project_context: Dict[str, Any],
    owned_files: Optional[str] = None,
    constraints: Optional[str] = None,
    acceptance_criteria: Optional[str] = None
) -> str:
    """Build a self-contained subagent assignment.

    Contains only its relevant goal, context, owned files/modules, constraints,
    and acceptance criteria, avoiding full-prompt forwarding with generic qualifiers.
    """
    proj_name = project_context.get("name", "unknown")
    proj_path = project_context.get("path", ".")

    if not owned_files:
        if agent == "codex":
            owned_files = "Backend source files, data models, services, API routes, and backend tests (e.g., superagent/*.py, tests/test_backend*.py)."
        elif agent in ("agy", "antigravity"):
            owned_files = "Frontend UI components, views, styles, client-side workflows, and frontend tests (e.g., superagent/tui/*, tests/test_frontend*.py)."
        elif agent == "claude":
            owned_files = "Architectural contracts, type definitions, interface boundaries, and security test suites."
        else:
            owned_files = "Domain-specific project source files and associated unit tests."

    if not constraints:
        constraints = (
            f"Operate strictly within repository root '{proj_path}'. "
            "Maintain strict domain isolation: do not modify files owned by parallel subagents "
            "or shared tracker files (e.g. TODO.md, PRIORITY_TRACKER.md) concurrently."
        )

    if not acceptance_criteria:
        acceptance_criteria = (
            "Verify all changes by running relevant test suites without regressions. "
            "Deliver a concise summary of findings, modified files, and verification status."
        )

    return (
        f"Goal: {goal.strip()}\n"
        f"Context: Project '{proj_name}' (root: {proj_path}).\n"
        f"Owned files/modules: {owned_files}\n"
        f"Constraints: {constraints}\n"
        f"Acceptance criteria: {acceptance_criteria}"
    )

class SuperagentBrain:
    """SuperAgent Brain that supports both Gemini API and subscription-backed CLI First Responders (Codex / Antigravity)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        first_responder: str = "codex",
        available_agents: Optional[List[str]] = None
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.first_responder = first_responder.lower().strip()
        self.available_agents = [a.lower().strip() for a in available_agents] if available_agents is not None else ["codex", "agy"]
        self.client: Optional[genai.Client] = None
        self.supervisor = ProcessSupervisor()

        if HAVE_GEMINI and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                self.client = None

    def set_available_agents(self, agents: List[str]) -> None:
        cleaned = [a.lower().strip() for a in agents if a.strip()]
        self.available_agents = cleaned if cleaned else ["codex"]

    def determine_effort(self, prompt: str, explicit_effort: Optional[str] = None) -> str:
        return determine_effort(prompt, explicit_effort)

    def derive_subagent_goal(self, prompt: str, agent: str) -> str:
        return derive_subagent_goal(prompt, agent)

    def build_scoped_assignment(
        self,
        agent: str,
        goal: str,
        project_context: Dict[str, Any],
        owned_files: Optional[str] = None,
        constraints: Optional[str] = None,
        acceptance_criteria: Optional[str] = None
    ) -> str:
        return build_scoped_assignment(
            agent, goal, project_context, owned_files, constraints, acceptance_criteria
        )

    def get_system_instruction(self) -> str:
        capabilities_map = {
            "codex": "- `codex`: OpenAI Codex CLI (ChatGPT Plus). Best for deep Python, backend logic, refactors, algorithms, test runs.",
            "agy": "- `agy`: Google Antigravity CLI (Google AI Pro). Best for full-stack exploration, frontend/TypeScript, file auditing, fast passes.",
            "claude": "- `claude`: Anthropic Claude Code CLI. Best for architectural edge cases, clean typing, refactoring.",
        }
        fleet_desc = "\n".join(capabilities_map[a] for a in self.available_agents if a in capabilities_map)
        agent_names = ", ".join(self.available_agents)
        return (
            "You are SuperAgent, the intelligent coordinator and First Responder for a fleet of subscription-backed coding CLIs:\n"
            f"{fleet_desc}\n\n"
            "Your responsibilities:\n"
            "1. Direct Conversation (action='reply'):\n"
            "   If the prompt is conversational, an explanation, a question about the project, or a confirmation:\n"
            "   - Set action = 'reply' and supply the direct response in reply_text. Do NOT delegate.\n\n"
            "2. Delegation (action='delegate'):\n"
            "   If the prompt asks for code changes, bug fixes, testing, or auditing:\n"
            f"   - IMPORTANT: Only delegate to active fleet agents: {agent_names}. Never use disabled or unavailable agents.\n"
            "   - If the user asks for multiple agents (e.g. 'spawn 2 subagents to review...'), create MULTIPLE tasks in the `tasks` array!\n"
            "   - For each task, choose:\n"
            f"     * `agent`: one of [{agent_names}].\n"
            "     * `effort`:\n"
            "       - 'low': Default for routine tasks (status checks, typos, formatting, docstrings, variable renames, trivial syntax fixes).\n"
            "       - 'medium': Default for substantial work (feature implementations, unit tests, code reviews, audits, bug fixes).\n"
            "       - 'high': Reserve strictly for rare, explicitly justified complexity (deep architectural rewrites, complex concurrency/race conditions, or explicit user requests for high reasoning).\n"
            "     * `title`: short 2-4 word summary.\n"
            "     * `task_brief`: Self-contained assignment containing ONLY its relevant goal, context, owned files/modules, constraints, and acceptance criteria.\n"
            "       DO NOT forward the full original prompt with a generic frontend/backend qualifier (e.g. never do 'Focus on backend: <original prompt>').\n"
            "   - SMART TASK SLICING: If the user provides multiple tasks, divide them so each parallel agent owns distinct non-overlapping files (never the same file concurrently). Keep tasks atomic so workers finish in 2-4 minutes.\n"
            "   - Set `reasoning`: 1 sentence explaining the multi-agent plan.\n"
        )

    @property
    def is_gemini_active(self) -> bool:
        return self.client is not None

    @property
    def mode_description(self) -> str:
        if self.is_gemini_active:
            return "Gemini 2.5 Flash API"
        return f"{self.first_responder.upper()} CLI (Subscription Mode, Low Effort)"

    def set_api_key(self, api_key: str) -> bool:
        self.api_key = api_key
        if HAVE_GEMINI and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                return True
            except Exception:
                self.client = None
                return False
        return False

    def set_first_responder(self, agent: str) -> None:
        if agent.lower() in ("codex", "agy", "antigravity"):
            self.first_responder = "agy" if agent.lower() in ("agy", "antigravity") else "codex"

    async def triage(
        self,
        user_prompt: str,
        project_context: Dict[str, Any],
        on_status: Optional[Callable[[str], None]] = None
    ) -> TriageDecision:
        clean = user_prompt.strip()

        # 1. Tier 1 (Fast Agent / Triage - 0ms):
        # Checks for direct conversational queries, explicit @agent routing, or minor atomic edits.
        fast = self._fast_triage(clean, project_context)
        if fast is not None:
            return fast

        # 2. Tier 2 (The Planning Model):
        # Non-trivial, complex, or multi-domain tasks are evaluated by the Planning Brain
        # to dynamically determine fleet decomposition, agent roles, and adaptive effort.
        if on_status:
            on_status(f"🧠 Planning Model ({self.mode_description}) formulating fleet decomposition...")
        return await self._run_planning_model(clean, project_context)

    def _fast_triage(self, prompt: str, project_context: Dict[str, Any]) -> Optional[TriageDecision]:
        """Tier 1 Fast Triage: handles conversational queries, explicit @agent commands, and minor edits in 0ms."""
        clean = prompt.strip()
        lower = clean.lower()
        default_agent = project_context.get("default_agent", "codex")

        # 1. Conversational greetings / questions / confirmations
        conversational_phrases = [
            "respond yes", "say yes", "yes", "no", "ok", "okay",
            "hello", "hi", "hey", "who are you", "what can you do",
            "help", "status", "test", "ping"
        ]

        if lower in conversational_phrases or lower.startswith("respond with ") or lower.startswith("say "):
            if "yes" in lower:
                reply = "Yes! SuperAgent is online and ready."
            elif any(w in lower for w in ("hello", "hi", "hey")):
                reply = f"Hello! SuperAgent coordinator is active for project '{project_context.get('name', 'unknown')}'. How can I help you today?"
            elif lower.startswith("say "):
                reply = prompt[4:].strip()
            elif lower.startswith("respond with "):
                reply = prompt[13:].strip()
            else:
                reply = "SuperAgent received your message. All systems are operational."

            return TriageDecision(
                action="reply",
                reply_text=reply,
                reasoning="Instant conversational reply."
            )

        # 2. Explicit agent routing: e.g. @codex:high, @agy:low, @claude
        agent_match = re.match(r"^@(codex|agy|claude|antigravity)(?::(low|medium|high))?\s+(.*)$", clean, re.IGNORECASE | re.DOTALL)
        if agent_match:
            target_raw = agent_match.group(1).lower()
            target_agent = "agy" if target_raw in ("agy", "antigravity") else target_raw
            explicit_effort = agent_match.group(2).lower() if agent_match.group(2) else None
            task_brief = agent_match.group(3).strip()
            effort = self.determine_effort(task_brief, explicit_effort)

            if target_agent not in self.available_agents:
                avail_str = ", ".join(f"@{a}" for a in self.available_agents)
                return TriageDecision(
                    action="reply",
                    reply_text=f"'{target_agent.upper()}' CLI is disabled or not subscribed.\nActive fleet agents: {avail_str}. (You can enable it if subscribed via ':agent enable {target_agent}')",
                    reasoning=f"Agent '{target_agent}' is not enabled in fleet."
                )

            return TriageDecision(
                action="delegate",
                tasks=[SubtaskItem(
                    agent=target_agent,
                    effort=effort,
                    title=task_brief[:30],
                    task_brief=task_brief
                )],
                reasoning=f"Explicitly routed to {target_agent.upper()} CLI with {effort} effort."
            )

        # 3. Simple / Minor atomic tweak (typos, single comments, docstrings)
        minor_keywords = ["typo", "fix spelling", "rename variable", "docstring", "comment", "formatting", "single line"]
        if any(k in lower for k in minor_keywords) and len(clean.split()) < 10:
            return TriageDecision(
                action="delegate",
                tasks=[SubtaskItem(
                    agent=default_agent,
                    effort="low",
                    title=clean[:30],
                    task_brief=clean
                )],
                reasoning="Minor atomic edit routed directly with low effort."
            )

        # 4. Short conversational fallback (<4 words and no action verbs)
        code_verbs = [
            "fix", "refactor", "write", "add", "create", "implement", "update",
            "delete", "remove", "test", "build", "debug", "check", "audit",
            "suggestions", "review", "inspect", "analyze", "scan", "spawn"
        ]
        is_coding = any(lower.startswith(v) or f" {v} " in lower for v in code_verbs)
        if not is_coding and len(clean.split()) < 4:
            return TriageDecision(
                action="reply",
                reply_text="I'm standing by. Give me a task or instructions for the fleet.",
                reasoning="Short conversational prompt."
            )

        # All other tasks are non-trivial / complex: need the Planning Model!
        return None

    def _get_project_knowledge(self, project_path: Optional[str]) -> str:
        """Reads project AGENTS.md or README.md if present to inform triage."""
        if not project_path:
            return "No project path provided."
        p = Path(project_path)
        for fname in ("AGENTS.md", "CLAUDE.md", "README.md"):
            doc_file = p / fname
            if doc_file.exists():
                try:
                    content = doc_file.read_text(encoding="utf-8")
                    return content[:1500]  # First 1500 chars for context
                except Exception:
                    pass
        return "Standard software project."

    def _local_heuristic_triage(self, prompt: str, project_context: Dict[str, Any]) -> TriageDecision:
        """Fallback local heuristic decomposition."""
        fast = self._fast_triage(prompt, project_context)
        if fast is not None:
            return fast

        clean = prompt.strip()
        lower = clean.lower()
        default_agent = project_context.get("default_agent", "codex")

        # Effort estimation: low for routine, medium for substantial, high for rare justified complexity
        effort = self.determine_effort(clean)

        # Check for natural language explicit agent directives (e.g. "use agy", "overtake with agy", "let agy take over")
        agent_directive = re.search(
            r"\b(?:use|with|run\s+on|overtake\s+with|switch\s+to|route\s+to|ask|let)\s+(codex|agy|claude|antigravity)\b",
            lower
        )
        if not agent_directive:
            agent_directive = re.search(
                r"\b(codex|agy|claude|antigravity)\s+(?:to\s+)?(?:take\s+over|overtake|run|handle|do\s+this|fix\s+this)\b",
                lower
            )

        # Multi-agent intent or broad audit/review/testing
        broad_keywords = ["audit", "review", "inspect", "check the project", "suggestions", "full-stack", "frontend and backend", "subagents", "agents"]
        is_broad = any(k in lower for k in broad_keywords)
        wants_three = "3" in lower or "three" in lower
        wants_multi = "subagents" in lower or "agents" in lower or "parallel" in lower or "2 " in lower or "two" in lower or wants_three

        if agent_directive and not wants_multi:
            target_raw = agent_directive.group(1).lower()
            target_agent = "agy" if target_raw in ("agy", "antigravity") else target_raw
            if target_agent in self.available_agents:
                return TriageDecision(
                    action="delegate",
                    tasks=[SubtaskItem(
                        agent=target_agent,
                        effort=effort,
                        title=clean[:30],
                        task_brief=clean
                    )],
                    reasoning=f"Explicitly routed to requested agent {target_agent.upper()}."
                )

        if is_broad:
            primary = "codex" if "codex" in self.available_agents else self.available_agents[0]
            secondary = "agy" if "agy" in self.available_agents else primary

            primary_goal = self.derive_subagent_goal(clean, primary)
            secondary_goal = self.derive_subagent_goal(clean, secondary)

            tasks = [
                SubtaskItem(
                    agent=primary,
                    effort=effort,
                    title="Backend Architecture & Logic Audit",
                    task_brief=self.build_scoped_assignment(primary, primary_goal, project_context)
                ),
                SubtaskItem(
                    agent=secondary,
                    effort=effort,
                    title="Frontend & Workflow Audit",
                    task_brief=self.build_scoped_assignment(secondary, secondary_goal, project_context)
                )
            ]
            if wants_three:
                third_agent = "claude" if "claude" in self.available_agents and shutil.which("claude") else primary
                third_goal = self.derive_subagent_goal(clean, third_agent)
                tasks.append(
                    SubtaskItem(
                        agent=third_agent,
                        effort=effort,
                        title="Code Refactoring & Security Audit",
                        task_brief=self.build_scoped_assignment(third_agent, third_goal, project_context)
                    )
                )
            return TriageDecision(
                action="delegate",
                tasks=tasks,
                reasoning=f"Decomposed into {len(tasks)} parallel subagents with distinct scoped assignments."
            )

        if agent_directive:
            target_raw = agent_directive.group(1).lower()
            target_agent = "agy" if target_raw in ("agy", "antigravity") else target_raw
            if target_agent in self.available_agents:
                return TriageDecision(
                    action="delegate",
                    tasks=[SubtaskItem(
                        agent=target_agent,
                        effort=effort,
                        title=clean[:30],
                        task_brief=clean
                    )],
                    reasoning=f"Explicitly routed to requested agent {target_agent.upper()}."
                )

        fallback_agent = default_agent if default_agent in self.available_agents else self.available_agents[0]
        return TriageDecision(
            action="delegate",
            tasks=[SubtaskItem(
                agent=fallback_agent,
                effort=effort,
                title=clean[:30],
                task_brief=clean
            )],
            reasoning=f"Routed to default agent {fallback_agent.upper()}."
        )

    async def _run_planning_model(self, prompt: str, project_context: Dict[str, Any]) -> TriageDecision:
        """Tier 2 Planning Model: Evaluates complex task to dynamically determine fleet plan & effort."""
        fallback = self._local_heuristic_triage(prompt, project_context)
        default_agent = project_context.get("default_agent", "codex")
        project_notes = self._get_project_knowledge(project_context.get("path"))

        # 1. If Gemini API key is available, use Gemini 2.5 Flash as the Planner
        if self.client:
            try:
                prompt_with_context = (
                    f"Active Project: {project_context.get('name', 'unknown')} ({project_context.get('path', '')})\n"
                    f"Default Agent: {default_agent}\n"
                    f"Project Guidelines:\n{project_notes}\n\n"
                    f"User Prompt: {prompt}"
                )
                response = await asyncio.wait_for(self.client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt_with_context,
                    config=types.GenerateContentConfig(
                        system_instruction=self.get_system_instruction(),
                        response_mime_type="application/json",
                        response_schema=TriageDecision,
                        temperature=0.2,
                    )
                ), timeout=25.0)
                if response.text:
                    data = json.loads(response.text)
                    plan = self._validate_plan(TriageDecision(**data))
                    for t in plan.tasks:
                        legacy_match = re.match(r"^Focus on (?:backend|frontend|code style|architecture)[^:]*:\s*(.*)$", t.task_brief, re.IGNORECASE)
                        if legacy_match:
                            goal = self.derive_subagent_goal(legacy_match.group(1), t.agent)
                            t.task_brief = self.build_scoped_assignment(t.agent, goal, project_context)
                    return plan
            except Exception:
                pass

        # 2. Subscription Mode: Use the Planning Model via CLI (agy or codex)
        capabilities_map = {
            "codex": "- codex: Backend logic, Python, database models, security, test suites, algorithms.",
            "agy": "- agy: Frontend UI, TypeScript, React components, client state, responsive design, workflows.",
            "claude": "- claude: Refactoring, type-checking, architecture edge cases.",
        }
        fleet_desc = "\n".join(capabilities_map[a] for a in self.available_agents if a in capabilities_map)
        agent_names = ", ".join(self.available_agents)
        agents_choice = "|".join(self.available_agents)

        planning_prompt = (
            "You are the SuperAgent Planning Model and fleet coordinator.\n"
            f"Active Project: {project_context.get('name', 'unknown')}\n"
            f"Project Guidelines Context:\n{project_notes}\n\n"
            f"User Goal: {prompt}\n\n"
            f"Available Fleet Capabilities ({agent_names}):\n"
            f"{fleet_desc}\n\n"
            "Instructions:\n"
            f"- IMPORTANT: Only use available fleet agents: {agent_names}. Do NOT delegate to any other agent.\n"
            "- If the task is focused on a single domain or straightforward, output 1 subagent task.\n"
            "- If the task is complex, multi-domain, full-stack, or broad audit/review/testing, decompose it into 2 or more complementary subagent tasks running in parallel across the fleet.\n"
            "- If the user specifies a number of subagents or tasks (e.g. 'spawn 2 subagents', 'spawn 3 subagents'), respect their request and output that number of DELEGATE lines for complementary fleet agents.\n"
            "- REASONING EFFORT SELECTION:\n"
            "  * Default to 'low' effort for routine tasks (e.g. status checks, typos, formatting, docstrings, variable renames, simple file inspections).\n"
            "  * Default to 'medium' effort for substantial work (e.g. feature implementations, unit tests, code reviews, audits, bug fixes).\n"
            "  * Reserve 'high' effort strictly for rare, explicitly justified complexity (e.g. deep architectural rewrites, complex concurrency/race conditions, or explicit user requests for high reasoning).\n"
            "- TASK PROMPT GENERATION (CRITICAL):\n"
            "  * Rewrite each subagent prompt into a self-contained assignment containing ONLY its relevant goal, context, owned files/modules, constraints, and acceptance criteria.\n"
            "  * NEVER forward the full original prompt with a generic frontend/backend qualifier (e.g. 'Focus on backend: ...').\n"
            "  * Ensure each assignment is fully self-contained and scoped exclusively to that agent's domain.\n"
            "- SMART TASK DECOMPOSITION & FILE ISOLATION:\n"
            "  * If the user asks for multiple items (e.g. 4 tasks on 2 agents, or multiple priorities):\n"
            "    1. Prepare a numbered task checklist in PLAN.\n"
            "    2. Divide the tasks so each parallel subagent gets a strictly non-overlapping file/module domain (e.g. Agent 1 owns backend API only; Agent 2 owns frontend UI only).\n"
            "    3. NEVER command parallel subagents to create or write to the same file or shared tracker simultaneously.\n"
            "    4. Limit each subagent to 1-2 focused, testable tasks per turn so it finishes cleanly in 2-4 minutes without hanging.\n"
            "- For a direct answer instead of delegation, output REPLY: <answer>.\n"
            "- Format delegation strictly as:\n"
            "PLAN: <1-sentence overview of coordination strategy>\n"
            f"DELEGATE: <agent: {agents_choice}> | effort: <low|medium|high> | <actionable task brief>\n"
        )

        candidate_agents = [self.first_responder] + [a for a in self.available_agents if a != self.first_responder]

        for cli_agent in candidate_agents:
            try:
                cwd = validate_project_path(project_context.get("path", ""))
                from superagent.runner import resolve_executable
                cmd = agent_command(cli_agent, planning_prompt, cwd, 'low', 'read-only')
                cmd = resolve_executable(cmd[0]) + cmd[1:]
                async with self.supervisor.process(
                    *cmd,
                    cwd=cwd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                ) as proc:
                    stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=25.0)
                    if proc.returncode != 0:
                        raise RuntimeError(f'Planner exited with code {proc.returncode}')
                output = stdout_bytes.decode("utf-8", errors="replace").strip()
                parsed_plan = self._parse_planner_output(output)
                for t in parsed_plan.tasks:
                    legacy_match = re.match(r"^Focus on (?:backend|frontend|code style|architecture)[^:]*:\s*(.*)$", t.task_brief, re.IGNORECASE)
                    if legacy_match:
                        goal = self.derive_subagent_goal(legacy_match.group(1), t.agent)
                        t.task_brief = self.build_scoped_assignment(t.agent, goal, project_context)
                return parsed_plan

            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                fallback.reasoning += f" Planning fallback: TimeoutError: {exc}"
                break
            except Exception as exc:
                fallback.reasoning += f" Planning fallback: {type(exc).__name__}: {exc}"
                continue

        return fallback

    def _validate_plan(self, decision: TriageDecision) -> TriageDecision:
        if decision.action == "reply":
            if not decision.reply_text or not decision.reply_text.strip() or decision.tasks:
                raise ValueError("Planner reply must contain text and no tasks.")
        elif decision.action == "delegate":
            if not 1 <= len(decision.tasks) <= 16:
                raise ValueError("Planner must return between 1 and 16 tasks.")
            for task in decision.tasks:
                if (task.agent not in self.available_agents
                        or task.effort not in ("low", "medium", "high")
                        or not task.task_brief.strip()):
                    raise ValueError("Planner returned an invalid task.")
        else:
            raise ValueError("Planner returned an invalid action.")
        return decision

    def _parse_planner_output(self, output: str) -> TriageDecision:
        """Parse final stdout only, preserving instruction text verbatim."""
        from superagent.runner import strip_ansi
        output = _extract_text_from_cli_output(output)
        lines = [strip_ansi(line).strip() for line in output.splitlines()]
        # Codex can echo the prompt before its final response marker.
        markers = [i for i, line in enumerate(lines) if line.lower() in ("codex", "codex:")]
        if markers:
            lines = lines[markers[-1] + 1:]
        plan = ""
        reply = None
        tasks = []
        seen = set()
        for line in lines:
            line = re.sub(r"^(?:[-*] )?\*\*(PLAN|DELEGATE|REPLY):\*\*", r"\1:", line)
            line = re.sub(r"^[-*] (?=(?:PLAN|DELEGATE|REPLY):)", "", line)
            if line.startswith("PLAN:"):
                plan = line.partition(":")[2].strip()
            elif line.startswith("DELEGATE:"):
                parts = line.partition(":")[2].split("|", 2)
                if len(parts) != 3 or not parts[1].strip().startswith("effort:"):
                    raise ValueError("Malformed planner delegation.")
                agent = parts[0].strip().strip("`*").lower()
                effort = parts[1].strip().partition(":")[2].strip().strip("`*").lower()
                brief = parts[2].strip()
                if brief not in seen:
                    seen.add(brief)
                    tasks.append(SubtaskItem(agent=agent, effort=effort,
                                             title=brief[:30], task_brief=brief))
            elif line.startswith("REPLY:"):
                if reply is not None:
                    raise ValueError("Planner returned multiple replies.")
                reply = line.partition(":")[2].strip()
        if reply is not None:
            return self._validate_plan(TriageDecision(
                action="reply", reply_text=reply, tasks=tasks, reasoning=plan))
        return self._validate_plan(TriageDecision(action="delegate", tasks=tasks, reasoning=plan))

    async def synthesize_results(
        self,
        user_prompt: str,
        plan_reasoning: str,
        task_records: List[Any],
        project_context: Dict[str, Any]
    ) -> str:
        """Synthesizes the findings and outputs of completed subagents into a unified coordinator report."""
        if not task_records:
            return "No subagent tasks were executed."

        reports = []
        for t in task_records:
            clean = getattr(t, "clean_output", "").strip()
            report_out = clean if clean else "No output recorded."
            diff = t.diff_summary or "No file changes detected."
            err = t.error_summary or "None"
            reports.append(
                f"Subagent [{t.agent.upper()} ({t.effort} effort)] - Status: {t.status} ({t.elapsed_formatted})\n"
                f"Objective: {t.task_desc}\n"
                f"Output / Findings:\n{report_out}\n"
                f"Modified Files: {diff}\n"
                f"Errors: {err}"
            )
        reports_joined = "\n\n---\n\n".join(reports)

        # A process exit is not task completion when the worker needs a reply.
        # Keep the unresolved question actionable rather than asking a model to
        # turn a partial fleet result into a completion report.
        unfinished = [t for t in task_records
                      if t.status in ('QUEUED', 'RUNNING', 'TESTING', 'AWAITING_INPUT')]
        if unfinished:
            pending = []
            for task in unfinished:
                choice = getattr(task, 'pending_choice', None)
                detail = f'Task {task.task_id}: {task.status}'
                if choice:
                    detail += f'\nQuestion: {choice.question}'
                    if choice.choices:
                        detail += '\nChoices: ' + ', '.join(choice.choices)
                    detail += f'\nFree-text answer: {"allowed" if choice.allow_free_text else "not allowed"}'
                pending.append(detail)
            return ('Fleet execution is awaiting input or still running.\n\n'
                    + '\n\n'.join(pending) + '\n\n' + reports_joined)

        # 1. If Gemini Flash API is available, generate an intelligent synthesis
        if self.client:
            try:
                prompt = (
                    f"User Prompt: {user_prompt}\n"
                    f"Initial Plan: {plan_reasoning}\n\n"
                    f"Subagent Reports:\n{reports_joined}\n\n"
                    "Synthesize the work done by the fleet into a comprehensive coordinator report addressing the user's prompt. Provide:\n"
                    "1. Executive Summary & Answer: Fulfill the user's specific request directly (e.g. if they asked for a compiled list, merge and deduplicate findings into that list).\n"
                    "2. Subagent Contributions: Key findings or code produced by each subagent.\n"
                    "3. Any modified files, git status, and recommended next steps."
                )
                response = await asyncio.wait_for(self.client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                    )
                ), timeout=45.0)
                if response.text:
                    return response.text.strip()
            except Exception:
                pass

        # 2. Subscription Mode: Use Candidate CLIs (first responder or next available) to synthesize
        candidate_agents = [self.first_responder] + [a for a in self.available_agents if a != self.first_responder]
        for cli_agent in candidate_agents:
            try:
                cwd = validate_project_path(project_context.get('path', ''))
                from superagent.runner import resolve_executable, strip_ansi
                synthesis_prompt = (
                    "You are the SuperAgent Fleet Coordinator.\n"
                    f"User Request: {user_prompt}\n\n"
                    f"Subagent Reports:\n{reports_joined}\n\n"
                    "Instructions:\n"
                    "- Address the user's request thoroughly using the subagents' outputs.\n"
                    "- If the user asked for a compiled or unified list (such as top 10 improvements or recommendations), compile, merge, and deduplicate findings into that unified list.\n"
                    "- Summarize what each subagent accomplished.\n"
                    "- Highlight any modified files or next steps.\n"
                    "Output your report directly in clean Markdown without conversational preamble."
                )
                cmd = agent_command(cli_agent, synthesis_prompt, cwd, 'low', 'read-only')
                cmd = resolve_executable(cmd[0]) + cmd[1:]
                async with self.supervisor.process(
                    *cmd,
                    cwd=cwd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                ) as proc:
                    stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
                    if proc.returncode != 0:
                        raise RuntimeError(f'Synthesizer exited with code {proc.returncode}')
                output = stdout_bytes.decode("utf-8", errors="replace").strip()
                output = _extract_text_from_cli_output(output)
                from superagent.runner import strip_ansi
                raw_lines = [strip_ansi(l) for l in output.splitlines()]

                # If output contains a 'codex' turn marker, take content after the last marker
                codex_idx = -1
                for idx, line in enumerate(raw_lines):
                    if line.strip().lower() in ("codex", "codex:"):
                        codex_idx = idx

                if codex_idx != -1 and codex_idx + 1 < len(raw_lines):
                    candidate_lines = raw_lines[codex_idx + 1:]
                else:
                    candidate_lines = raw_lines

                skip_cli_prefixes = (
                    "OpenAI Codex", "workdir:", "model:", "provider:", "approval:", "sandbox:",
                    "reasoning effort:", "reasoning summaries:", "session id:", "tokens used",
                    "Reading additional input", "--------", "You are the SuperAgent Fleet Coordinator."
                )
                cleaned_lines = [
                    l for l in candidate_lines
                    if not any(l.strip().startswith(p) for p in skip_cli_prefixes)
                    and l.strip().lower() not in ("user", "codex")
                ]
                if cleaned_lines:
                    result = "\n".join(cleaned_lines).strip()
                    if len(result) > 40:
                        successful = sum(1 for t in task_records if t.status == "DONE")
                        total = len(task_records)
                        header = f"• Fleet Execution: {successful}/{total} subagent task(s) completed successfully.\n\n"
                        return header + result
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                break
            except Exception:
                continue

        # 3. Structured synthesis fallback (never truncates findings)
        lines: List[str] = []
        successful = sum(1 for t in task_records if t.status == "DONE")
        total = len(task_records)
        lines.append(f"• Fleet Execution: {successful}/{total} subagent task(s) completed successfully.\n")

        for t in task_records:
            lines.append("")
            lines.append(f"### {t.agent.upper()} - {t.status} ({t.effort} effort, {t.elapsed_formatted})")
            lines.append(f"**Objective:** {t.task_desc}\n")
            if getattr(t, "diff_summary", None):
                lines.append(f"**Modified:** {t.diff_summary}\n")
            if getattr(t, "error_summary", None):
                lines.append(f"**Error:** {t.error_summary}\n")
            clean = getattr(t, "clean_output", "").strip()
            if clean:
                lines.append(f"\n**Findings & Deliverables:**\n\n{clean}")
            lines.append("")

        if any(getattr(t, "diff_summary", None) for t in task_records):
            lines.append("• Repository changes detected. Review git status or switch to Subagent Console for details.")
        else:
            lines.append("• Analysis complete. No repository file changes were detected.")

        return "\n".join(lines).strip()
