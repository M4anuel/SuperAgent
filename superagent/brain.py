from __future__ import annotations
import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
    HAVE_GEMINI = True
except ImportError:
    HAVE_GEMINI = False

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
     * `effort`: 'low' (quick typo/syntax), 'medium' (standard features/audits), 'high' (deep architectural refactors).
     * `title`: short 2-4 word summary.
     * `task_brief`: clear, self-contained instruction for that worker.
   - Set `reasoning`: 1 sentence explaining the multi-agent plan.
"""

class SuperagentBrain:
    """SuperAgent Brain that supports both Gemini API and subscription-backed CLI First Responders (Codex / Antigravity)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        first_responder: str = "codex",
        available_agents: Optional[List[str]] = None
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.first_responder = first_responder.lower()  # "codex" or "agy"
        self.available_agents = [a.lower().strip() for a in (available_agents or ["codex", "agy"])]
        self.client: Optional[genai.Client] = None

        if HAVE_GEMINI and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                self.client = None

    def set_available_agents(self, agents: List[str]) -> None:
        cleaned = [a.lower().strip() for a in agents if a.strip()]
        self.available_agents = cleaned if cleaned else ["codex"]

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
            "     * `effort`: 'low' (quick typo/syntax), 'medium' (standard features/audits), 'high' (deep architectural refactors).\n"
            "     * `title`: short 2-4 word summary.\n"
            "     * `task_brief`: clear, self-contained instruction for that worker.\n"
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
        agent_match = re.match(r"^@(codex|agy|claude|antigravity)(?::(low|medium|high))?\s+(.*)$", clean, re.IGNORECASE)
        if agent_match:
            target_raw = agent_match.group(1).lower()
            target_agent = "agy" if target_raw in ("agy", "antigravity") else target_raw
            effort = agent_match.group(2).lower() if agent_match.group(2) else "medium"
            task_brief = agent_match.group(3).strip()

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

        # Effort estimation based on keywords
        high_effort_keywords = ["high effort", "deep reasoning", "deep think", "thorough", "complex", "architecture", "refactor", "rewrite", "redesign", "security"]
        low_effort_keywords = ["low effort", "fast pass", "quick pass", "minimal effort", "typo", "rename"]

        if any(k in lower for k in high_effort_keywords):
            effort = "high"
        elif any(k in lower for k in low_effort_keywords):
            effort = "low"
        else:
            effort = "medium"

        # Multi-agent intent or broad audit/review/testing
        broad_keywords = ["audit", "review", "inspect", "check the project", "suggestions", "full-stack", "frontend and backend", "subagents", "agents"]
        is_broad = any(k in lower for k in broad_keywords)
        wants_three = "3" in lower or "three" in lower

        if is_broad:
            primary = "codex" if "codex" in self.available_agents else self.available_agents[0]
            secondary = "agy" if "agy" in self.available_agents else primary

            tasks = [
                SubtaskItem(
                    agent=primary,
                    effort=effort,
                    title="Backend Architecture & Logic Audit",
                    task_brief=f"Focus on backend logic, data models, edge cases, and test suites: {clean}"
                ),
                SubtaskItem(
                    agent=secondary,
                    effort=effort,
                    title="Frontend & Workflow Audit",
                    task_brief=f"Focus on frontend components, user workflows, and UI suggestions: {clean}"
                )
            ]
            if wants_three:
                third_agent = "claude" if "claude" in self.available_agents and shutil.which("claude") else primary
                tasks.append(
                    SubtaskItem(
                        agent=third_agent,
                        effort=effort,
                        title="Code Refactoring & Security Audit",
                        task_brief=f"Focus on code style, typing, security, and refactoring: {clean}"
                    )
                )
            return TriageDecision(
                action="delegate",
                tasks=tasks,
                reasoning=f"Decomposed into {len(tasks)} parallel subagents for complementary audit."
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
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt_with_context,
                    config=types.GenerateContentConfig(
                        system_instruction=self.get_system_instruction(),
                        response_mime_type="application/json",
                        response_schema=TriageDecision,
                        temperature=0.2,
                    )
                )
                if response.text:
                    data = json.loads(response.text)
                    return TriageDecision(**data)
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
            "- Format strictly as:\n"
            "PLAN: <1-sentence overview of coordination strategy>\n"
            f"DELEGATE: <agent: {agents_choice}> | effort: <low|medium|high> | <actionable task brief>\n"
        )

        cwd = project_context.get("path") or os.getcwd()
        cli_agent = self.first_responder

        try:
            from superagent.runner import resolve_executable
            if cli_agent == "agy":
                exec_prefix = resolve_executable("agy")
                cmd = exec_prefix + ["--dangerously-skip-permissions", "--effort", "low", "-p", planning_prompt]
            else:
                exec_prefix = resolve_executable("codex")
                cmd = exec_prefix + [
                    "exec",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c", 'model_reasoning_effort="low"',
                    "-C", cwd,
                    planning_prompt
                ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=25.0)
            output = stdout_bytes.decode("utf-8", errors="replace").strip()
            lines = [l.strip() for l in output.splitlines() if l.strip()]

            # Look for PLAN: and DELEGATE: lines
            plan_line = next((l.split("PLAN:", 1)[1].strip() for l in lines if "PLAN:" in l), "")
            delegate_lines = [l for l in lines if "DELEGATE:" in l]

            if delegate_lines:
                tasks: List[SubtaskItem] = []
                seen_briefs = set()
                for d_line in delegate_lines:
                    if "<agent:" in d_line or "<low|" in d_line:
                        continue  # skip echoes
                    parts = d_line.split("DELEGATE:", 1)[1].split("|")
                    agent = parts[0].replace("*", "").replace("`", "").strip().lower() if len(parts) > 0 else default_agent
                    effort = "medium"
                    if len(parts) > 1 and "effort:" in parts[1]:
                        effort = parts[1].replace("effort:", "").replace("*", "").replace("`", "").strip().lower()
                    task_brief = parts[2].replace("*", "").replace("`", "").strip() if len(parts) > 2 else prompt

                    if agent not in self.available_agents:
                        agent = default_agent if default_agent in self.available_agents else self.available_agents[0]
                    if effort not in ("low", "medium", "high"):
                        effort = "medium"

                    if task_brief in seen_briefs:
                        continue
                    seen_briefs.add(task_brief)

                    title = task_brief[:30] + "..." if len(task_brief) > 30 else task_brief
                    tasks.append(SubtaskItem(
                        agent=agent,
                        effort=effort,
                        title=title,
                        task_brief=task_brief
                    ))

                if tasks:
                    reasoning = plan_line or f"Decomposed into {len(tasks)} subagent task(s) by Planning Model."
                    return TriageDecision(
                        action="delegate",
                        tasks=tasks,
                        reasoning=reasoning
                    )

            # Direct response check if no DELEGATE lines
            resp_lines: List[str] = []
            for l in lines:
                if (
                    l.startswith("OpenAI Codex")
                    or l.startswith("workdir:")
                    or l.startswith("model:")
                    or l.startswith("provider:")
                    or l.startswith("approval:")
                    or l.startswith("sandbox:")
                    or l.startswith("reasoning effort:")
                    or l.startswith("reasoning summaries:")
                    or l.startswith("session id:")
                    or l.startswith("tokens used")
                    or l.startswith("--------")
                    or l == "user"
                    or l == "codex"
                    or l.startswith("Reading additional input")
                    or l.startswith("PLAN:")
                ):
                    continue
                resp_lines.append(l)

            if resp_lines and not any(k in prompt.lower() for k in ("fix", "test", "audit", "check", "write", "create", "refactor", "code")):
                return TriageDecision(
                    action="reply",
                    reply_text="\n".join(resp_lines).strip(),
                    reasoning=f"Direct response from {cli_agent.upper()} Planning Model."
                )

        except Exception:
            pass

        return fallback

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

        # If Gemini Flash is available, generate an intelligent synthesis
        if self.client:
            try:
                reports = []
                for t in task_records:
                    out = t.clean_output[:1000] if getattr(t, "clean_output", None) else "No output."
                    diff = t.diff_summary or "No file changes detected."
                    reports.append(
                        f"Subagent [{t.agent.upper()} ({t.effort} effort)] - Status: {t.status} ({t.elapsed_formatted})\n"
                        f"Brief: {t.task_desc}\n"
                        f"Output: {out}\n"
                        f"Modified Files: {diff}"
                    )
                reports_joined = "\n\n".join(reports)
                prompt = (
                    f"User Prompt: {user_prompt}\n"
                    f"Initial Plan: {plan_reasoning}\n\n"
                    f"Subagent Reports:\n{reports_joined}\n\n"
                    "Synthesize the work done by the fleet. Provide:\n"
                    "1. A concise overview of what was accomplished.\n"
                    "2. Key insights or conclusions from each subagent.\n"
                    "3. Any modified files or next steps for the user."
                )
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                    )
                )
                if response.text:
                    return response.text.strip()
            except Exception:
                pass

        # Structured synthesis (instant, reliable, no external API needed)
        lines: List[str] = []
        successful = sum(1 for t in task_records if t.status == "DONE")
        total = len(task_records)
        lines.append(f"• Fleet Execution: {successful}/{total} subagent task(s) completed successfully.")
        
        for t in task_records:
            status_icon = "✔" if t.status == "DONE" else "✖"
            header = f"• [{t.agent.upper()}] ({t.effort} effort, {t.elapsed_formatted}) {status_icon} {t.status}:"
            lines.append(header)
            
            clean = getattr(t, "clean_output", "").strip()
            if clean:
                preview_lines = [l.strip() for l in clean.splitlines() if l.strip()]
                preview = " ".join(preview_lines[:3])
                if len(preview) > 280:
                    preview = preview[:277] + "..."
                lines.append(f"  Findings: {preview}")
            else:
                lines.append(f"  Task '{t.task_desc}' finished.")

            if getattr(t, "diff_summary", None):
                lines.append(f"  Modified: {t.diff_summary}")

        if any(getattr(t, "diff_summary", None) for t in task_records):
            lines.append("• Repository changes detected. Review git status or switch to Subagent Console for details.")
        else:
            lines.append("• Analysis complete. No repository file changes were committed.")

        return "\n".join(lines)
