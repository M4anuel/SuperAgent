from __future__ import annotations
import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List
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

    def __init__(self, api_key: Optional[str] = None, first_responder: str = "codex"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.first_responder = first_responder.lower()  # "codex" or "agy"
        self.client: Optional[genai.Client] = None

        if HAVE_GEMINI and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                self.client = None

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

    async def triage(self, user_prompt: str, project_context: Dict[str, Any]) -> TriageDecision:
        clean = user_prompt.strip()

        # 1. Fast path: check instantaneous local heuristics for common conversational cues & direct requests
        fast = self._local_heuristic_triage(clean, project_context)
        if fast.action == "reply":
            return fast

        # If user explicitly routed to an agent or asked for multi-agent delegation or clear coding task:
        # Return instantaneous local triage (0ms latency, zero UI lag or freezing!)
        lower = clean.lower()
        if (
            lower.startswith("@claude")
            or lower.startswith("@codex")
            or lower.startswith("@agy")
            or lower.startswith("@antigravity")
            or len(fast.tasks) > 1
            or fast.action == "delegate"
        ):
            return fast

        # 2. If Gemini API key is available, use Gemini Flash
        if self.client:
            try:
                project_notes = self._get_project_knowledge(project_context.get("path"))
                prompt_with_context = (
                    f"Active Project: {project_context.get('name', 'unknown')} ({project_context.get('path', '')})\n"
                    f"Default Agent: {project_context.get('default_agent', 'codex')}\n"
                    f"Project Guidelines:\n{project_notes}\n\n"
                    f"User Prompt: {clean}"
                )
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt_with_context,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_TRIAGE_INSTRUCTION,
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

        # 3. Subscription Mode: Use the low-effort CLI First Responder (Codex or Antigravity)
        return await self._cli_first_responder(clean, project_context, fallback_decision=fast)

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

    async def _cli_first_responder(
        self,
        prompt: str,
        project_context: Dict[str, Any],
        fallback_decision: TriageDecision
    ) -> TriageDecision:
        cli_agent = self.first_responder
        triage_prompt = (
            "You are the SuperAgent First Responder coordinator. "
            "Available fleet agents: codex (backend/logic), agy (frontend/tools), claude. "
            "If user prompt is conversational or a question, reply directly (do not write code). "
            "If user prompt requires coding, testing, or auditing: "
            "If user asks for multiple subagents (e.g. 'spawn 2 subagents'), output MULTIPLE DELEGATE lines! "
            "Format each task on its own line: DELEGATE: <agent: codex|agy|claude> | effort: <low|medium|high> | <task brief>. "
            f"User prompt: {prompt}"
        )

        cwd = project_context.get("path") or os.getcwd()

        try:
            from superagent.runner import resolve_executable
            if cli_agent == "codex":
                exec_prefix = resolve_executable("codex")
                cmd = exec_prefix + [
                    "exec",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c", 'model_reasoning_effort="low"',
                    "-C", cwd,
                    triage_prompt
                ]
            else:
                exec_prefix = resolve_executable("agy")
                cmd = exec_prefix + ["--dangerously-skip-permissions", "--effort", "low", "--print", triage_prompt]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            output = stdout_bytes.decode("utf-8", errors="replace").strip()
            lines = [l.strip() for l in output.splitlines() if l.strip()]

            # Look for one or more DELEGATE lines
            delegate_lines = [l for l in lines if "DELEGATE:" in l]
            if delegate_lines:
                tasks: List[SubtaskItem] = []
                seen_briefs = set()
                for d_line in delegate_lines:
                    if "<agent:" in d_line or "<low|" in d_line:
                        continue  # skip instruction echoes
                    parts = d_line.split("DELEGATE:", 1)[1].split("|")
                    agent = parts[0].strip().lower() if len(parts) > 0 else "codex"
                    effort = "medium"
                    if len(parts) > 1 and "effort:" in parts[1]:
                        effort = parts[1].replace("effort:", "").strip().lower()
                    task_brief = parts[2].strip() if len(parts) > 2 else prompt

                    if agent not in ("codex", "agy", "claude"):
                        agent = "codex"
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

                return TriageDecision(
                    action="delegate",
                    tasks=tasks,
                    reasoning=f"Decomposed into {len(tasks)} subagent task(s) by {cli_agent.upper()} First Responder."
                )

            # Direct response text
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
                ):
                    continue
                resp_lines.append(l)

            if resp_lines:
                return TriageDecision(
                    action="reply",
                    reply_text="\n".join(resp_lines).strip(),
                    reasoning=f"Direct response from {cli_agent.upper()} First Responder."
                )

        except Exception:
            pass

        return fallback_decision

    def _local_heuristic_triage(self, prompt: str, project_context: Dict[str, Any]) -> TriageDecision:
        clean = prompt.strip()
        lower = clean.lower()
        default_agent = project_context.get("default_agent", "codex")

        # Conversational cues
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

        # Check explicit @agent or @agent:effort routing: e.g. @codex:high, @agy:low, @claude
        agent_match = re.match(r"^@(codex|agy|claude|antigravity)(?::(low|medium|high))?\s+(.*)$", clean, re.IGNORECASE)
        if agent_match:
            target_raw = agent_match.group(1).lower()
            target_agent = "agy" if target_raw in ("agy", "antigravity") else target_raw
            effort = agent_match.group(2).lower() if agent_match.group(2) else "medium"
            task_brief = agent_match.group(3).strip()
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

        # Effort estimation based on prompt keywords
        high_effort_keywords = ["high effort", "deep reasoning", "deep think", "thorough", "complex", "architecture", "refactor", "rewrite", "redesign", "concurrency", "race condition", "security"]
        low_effort_keywords = ["low effort", "fast pass", "quick pass", "minimal effort", "typo", "rename", "docstring", "comment", "format", "single line"]

        if any(k in lower for k in high_effort_keywords):
            effort = "high"
            effort_reason = "High reasoning effort selected."
        elif any(k in lower for k in low_effort_keywords):
            effort = "low"
            effort_reason = "Low reasoning effort selected."
        else:
            effort = "medium"
            effort_reason = "Standard reasoning effort."

        # Check multi-agent intent: e.g. "spawn 2 subagents", "2 agents", "both agents", "codex and agy"
        multi_match = re.search(r"(\d+)\s+(?:sub)?agents?", lower)
        count = int(multi_match.group(1)) if multi_match else (
            2 if any(w in lower for w in ("two subagents", "two agents", "both agents", "codex and agy", "agy and codex")) else 1
        )

        # Code action verbs trigger delegation
        code_verbs = [
            "fix", "refactor", "write", "add", "create", "implement", "update",
            "delete", "remove", "test", "build", "debug", "check", "audit",
            "suggestions", "review", "inspect", "analyze", "scan"
        ]
        is_coding = any(lower.startswith(v) or f" {v} " in lower for v in code_verbs)

        if not is_coding and len(clean.split()) < 4 and count == 1:
            return TriageDecision(
                action="reply",
                reply_text="I'm standing by. Give me a task or instructions for the fleet.",
                reasoning="Short conversational prompt."
            )

        tasks: List[SubtaskItem] = []
        if count >= 2:
            # Multi-agent complementary decomposition
            tasks.append(SubtaskItem(
                agent="codex",
                effort=effort,
                title="Backend Architecture & Logic Audit",
                task_brief=f"Focus on backend logic, data models, edge cases, and test suites: {clean}"
            ))
            tasks.append(SubtaskItem(
                agent="agy" if shutil.which("agy") else "codex",
                effort=effort,
                title="Frontend & Workflow Audit",
                task_brief=f"Focus on frontend components, user workflows, and UI suggestions: {clean}"
            ))
            if count >= 3:
                tasks.append(SubtaskItem(
                    agent="claude" if shutil.which("claude") else "codex",
                    effort=effort,
                    title="Code Refactoring & Security Audit",
                    task_brief=f"Focus on code style, typing, security, and refactoring: {clean}"
                ))
            reasoning = f"Decomposed into {len(tasks)} parallel subagents for complementary audit ({effort_reason})."
        else:
            tasks.append(SubtaskItem(
                agent=default_agent,
                effort=effort,
                title=clean[:30],
                task_brief=clean
            ))
            reasoning = f"Routed to default agent {default_agent.upper()} ({effort_reason})."

        return TriageDecision(
            action="delegate",
            tasks=tasks,
            reasoning=reasoning
        )
