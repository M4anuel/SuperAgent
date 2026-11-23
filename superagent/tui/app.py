from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Dict, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane

from superagent.brain import SuperagentBrain
from superagent.config import SuperagentConfig, ProjectConfig
from superagent.git_utils import GitManager
from superagent.runner import SubagentRunner, TaskRecord
from superagent.tui.cards import SubagentCard

APP_CSS = """
Screen {
    background: $surface;
    color: $text;
}

#project-banner {
    dock: top;
    height: 3;
    background: $primary-background;
    color: $text;
    padding: 0 1;
    border-bottom: heavy $accent;
}

#main-container {
    height: 1fr;
    margin: 1 1 0 1;
}

#cards-pane {
    width: 45%;
    border: round $primary;
    height: 100%;
    padding: 0 1;
}

#cards-header {
    dock: top;
    height: 2;
    background: $surface-darken-1;
    color: $accent;
    text-style: bold;
    padding: 0 1;
}

#cards-scroll {
    height: 1fr;
}

#log-pane {
    width: 55%;
    border: round $secondary;
    height: 100%;
    margin-left: 1;
    padding: 0;
}

#main-tabs {
    height: 100%;
}

#superagent-feed {
    height: 1fr;
    padding: 0 1;
}

#subagent-log {
    height: 1fr;
    padding: 0 1;
}

#input-container {
    dock: bottom;
    height: 4;
    padding: 0 1;
    margin-bottom: 1;
}

#prompt-input {
    border: heavy $accent;
}

SubagentCard {
    border: round $surface-lighten-2;
    height: 4;
    margin-bottom: 1;
    padding: 0 1;
    background: $surface-darken-1;
}

SubagentCard:focus {
    border: double $accent;
    background: $surface;
}
"""

class RunnerEvent(Message):
    """Thread-safe event from SubagentRunner dispatched to Textual."""
    def __init__(self, task: TaskRecord, event: str) -> None:
        super().__init__()
        self.task = task
        self.event = event

class SuperagentApp(App):
    CSS = APP_CSS
    TITLE = "SuperAgent Fleet Coordinator"
    
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+p", "switch_project_dialog", "Projects", show=True),
        Binding("ctrl+k", "cancel_active", "Cancel Task", show=True),
        Binding("ctrl+l", "clear_finished", "Clear Done", show=True),
        Binding("ctrl+y", "copy_active_log", "Copy Log", show=True),
    ]

    def copy_to_clipboard(self, text: str) -> None:
        """Copies text to clipboard using Textual OSC 52 and native Windows clipboard."""
        super().copy_to_clipboard(text)
        copied = False
        try:
            import win32clipboard, win32con
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            copied = True
        except Exception:
            pass

        if not copied:
            try:
                import subprocess
                subprocess.run(["clip"], input=text.encode("utf-16le"), check=False)
                copied = True
            except Exception:
                pass

        chars = len(text)
        lines = text.count("\n") + 1
        self.notify(f"Copied {lines} lines ({chars} chars) to clipboard!", title="Clipboard", timeout=2.0)

    def action_copy_active_log(self) -> None:
        task = self.runner.tasks.get(self.active_task_id) if self.active_task_id else None
        if task and task.log_lines:
            text = "\n".join(task.log_lines)
            self.copy_to_clipboard(text)
        else:
            self.notify("No active task log to copy.", title="Clipboard", severity="warning")

    def __init__(self, initial_project: Optional[str] = None):
        super().__init__()
        self.cfg = SuperagentConfig()
        if initial_project:
            self.cfg.set_active_project(initial_project)
        self.brain = SuperagentBrain(
            api_key=self.cfg.gemini_api_key,
            first_responder=self.cfg.first_responder
        )
        self.runner = SubagentRunner()
        self.active_task_id: Optional[str] = None
        self.card_widgets: Dict[str, SubagentCard] = {}
        
        # Subscribe runner events to threadsafe UI callbacks
        self.runner.subscribe(self._dispatch_runner_callback)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        # Top banner with active project details
        with Container(id="project-banner"):
            yield Static(self._render_banner(), id="banner-text")

        with Horizontal(id="main-container"):
            # Left pane: subagents cards
            with Vertical(id="cards-pane"):
                yield Label("⚡ ACTIVE FLEET SUBAGENTS", id="cards-header")
                with VerticalScroll(id="cards-scroll"):
                    yield Static("[dim]No subagents running. Enter a task below.[/dim]", id="cards-empty-notice")
            
            # Right pane: tabbed feed and console
            with Vertical(id="log-pane"):
                with TabbedContent(id="main-tabs"):
                    with TabPane("🤖 SuperAgent Feed", id="tab-superagent"):
                        yield RichLog(id="superagent-feed", highlight=True, markup=True, wrap=True)
                    with TabPane("💻 Subagent Console", id="tab-console"):
                        yield RichLog(id="subagent-log", highlight=True, markup=True, wrap=True)

        with Container(id="input-container"):
            yield Input(
                placeholder="Ask Superagent: '@claude <task>', '@codex <task>', '@agy <task>' or ':help'",
                id="prompt-input"
            )

        yield Footer()

    def on_mount(self) -> None:
        feed = self.query_one("#superagent-feed", RichLog)
        feed.write("[bold cyan]═════════════════════════════════════════════════════════════════════[/bold cyan]")
        feed.write("[bold white]🤖 SuperAgent Fleet Coordinator Online[/bold white]")
        active_proj = self.cfg.active_project
        pname = active_proj.name if active_proj else "None"
        agent = active_proj.default_agent.upper() if active_proj else "CODEX"
        feed.write(f"[dim]• Default Subagent: [/dim][bold green]{agent} CLI[/bold green]  [dim]| Active Project: [/dim][bold cyan]{pname}[/bold cyan]")
        feed.write(f"[bold green]• 🧠 First Responder Brain:[/bold green] [bold cyan]{self.brain.mode_description}[/bold cyan]")
        feed.write("[dim]• Conversational prompts answer directly. Coding tasks delegate with adaptive effort.[/dim]")
        feed.write("[dim]• Switch to 'Subagent Console' tab anytime to inspect live raw CLI output.[/dim]")
        feed.write("[bold cyan]═════════════════════════════════════════════════════════════════════[/bold cyan]\n")
        
        # Start milestone check every 20 seconds
        self.set_interval(20.0, self._check_running_milestones)

        # Focus input
        self.query_one("#prompt-input", Input).focus()

    def _check_running_milestones(self) -> None:
        feed = self.query_one("#superagent-feed", RichLog)
        for task in self.runner.tasks.values():
            if task.status == "RUNNING" and task.elapsed_seconds >= 20:
                feed.write(f"[dim]⏳ [{task.agent.upper()}] Task #{task.task_id} still executing ([yellow]{task.elapsed_formatted}[/yellow])...[/dim]")

    def _render_banner(self) -> str:
        proj = self.cfg.active_project
        if proj:
            git_mgr = GitManager(proj.path)
            branch = git_mgr.current_branch() if git_mgr.is_git_repo() else "n/a"
            return (
                f"[bold cyan]PROJECT:[/bold cyan] [bold white]{proj.name}[/bold white]  "
                f"[dim]PATH:[/dim] {proj.path}  "
                f"[dim]BRANCH:[/dim] [yellow]{branch}[/yellow]  "
                f"[dim]DEFAULT AGENT:[/dim] [green]{proj.default_agent.upper()}[/green]"
            )
        return "[red]No active project selected. Type ':projects' to see registered projects.[/red]"

    def update_banner(self) -> None:
        banner = self.query_one("#banner-text", Static)
        banner.update(self._render_banner())

    def _dispatch_runner_callback(self, task: TaskRecord, event: str) -> None:
        self.post_message(RunnerEvent(task, event))

    def on_runner_event(self, message: RunnerEvent) -> None:
        self._handle_runner_event_sync(message.task, message.event)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tabbed_content.active == "tab-console" and self.active_task_id:
            self.select_task(self.active_task_id)

    def _handle_runner_event_sync(self, task: TaskRecord, event: str) -> None:
        feed = self.query_one("#superagent-feed", RichLog)

        if event == "new":
            scroll = self.query_one("#cards-scroll", VerticalScroll)
            empty_notice = self.query("#cards-empty-notice")
            if empty_notice:
                empty_notice.remove()

            card = SubagentCard(task)
            card.is_active_selection = (self.active_task_id == task.task_id)
            self.card_widgets[task.task_id] = card
            scroll.mount(card)

            # If no active selection, select this one
            if not self.active_task_id:
                self.select_task(task.task_id)

            feed.write(f"[dim]⚡ Task #{task.task_id} queued for {task.agent.upper()} CLI.[/dim]")

        elif event == "log":
            # If this is the currently selected task, append to Subagent Console RichLog
            if self.active_task_id == task.task_id and task.log_lines:
                log_widget = self.query_one("#subagent-log", RichLog)
                log_widget.write(task.log_lines[-1])

        elif event == "status":
            card = self.card_widgets.get(task.task_id)
            if card:
                card.refresh_display()

            if task.status == "RUNNING":
                feed.write(f"[bold cyan]🚀 [{task.agent.upper()}] actively executing Task #{task.task_id}...[/bold cyan]")
            elif task.status == "DONE":
                feed.write(f"\n[bold green]╭─── [SuperAgent Report: Task Completed] ──────────────────────────[/bold green]")
                feed.write(f"[bold green]│[/bold green] ✔ [bold white][{task.agent.upper()}][/bold white] finished Task #{task.task_id} in [yellow]⏱ {task.elapsed_formatted}[/yellow]")
                if task.diff_summary:
                    feed.write(f"[bold green]│[/bold green] 📊 [dim]Modified repository files:[/dim]\n[yellow]{task.diff_summary}[/yellow]")
                else:
                    feed.write(f"[bold green]│[/bold green] ℹ [dim]No git diff detected or changes committed.[/dim]")
                feed.write(f"[bold green]│[/bold green] 💡 [dim]SuperAgent is standing by for your next instruction.[/dim]")
                feed.write(f"[bold green]╰─────────────────────────────────────────────────────────────────[/bold green]\n")
            elif task.status == "FAILED":
                feed.write(f"\n[bold red]╭─── [SuperAgent Alert: Task Failed] ─────────────────────────────[/bold red]")
                feed.write(f"[bold red]│[/bold red] ✖ [bold white][{task.agent.upper()}][/bold white] exited with code {task.returncode} after [yellow]⏱ {task.elapsed_formatted}[/yellow]")
                if task.error_summary:
                    feed.write(f"[bold red]│[/bold red] 🔍 [dim]Recent error output:[/dim]\n[red]{task.error_summary}[/red]")
                feed.write(f"[bold red]│[/bold red] 💡 [dim]Switch to 'Subagent Console' tab to inspect the full trace.[/dim]")
                feed.write(f"[bold red]╰─────────────────────────────────────────────────────────────────[/bold red]\n")
            elif task.status == "CANCELLED":
                feed.write(f"[magenta]🚫 [{task.agent.upper()}] Task #{task.task_id} was cancelled by user.[/magenta]\n")

    def on_subagent_card_selected(self, message: SubagentCard.Selected) -> None:
        self.select_task(message.card.task_record.task_id)

    def select_task(self, task_id: str) -> None:
        self.active_task_id = task_id
        for tid, card in self.card_widgets.items():
            card.is_active_selection = (tid == task_id)
            card.refresh_display()

        task = self.runner.tasks.get(task_id)
        if task:
            log_widget = self.query_one("#subagent-log", RichLog)
            log_widget.clear()
            log_widget.write(f"[bold cyan]=== Task #{task.task_id} for {task.agent.upper()} ({task.effort.upper()} effort) ===[/bold cyan]")
            log_widget.write(f"[dim]Project: {task.project_name} | Directory: {task.cwd}[/dim]")
            log_widget.write(f"[bold white]Objective:[/bold white] {task.task_desc}")
            log_widget.write("[dim]─────────────────────────────────────────────────────────────────[/dim]\n")
            for line in task.log_lines:
                log_widget.write(line)

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        prompt = message.value.strip()
        if not prompt:
            return

        message.input.clear()
        feed = self.query_one("#superagent-feed", RichLog)

        # Check for commands
        if prompt.startswith(":"):
            await self._handle_command(prompt)
            return

        # Check project context
        proj = self.cfg.active_project
        if not proj:
            feed.write("[red]Error: No active project. Register one with :add <name> <path>[/red]")
            return

        # 1. Echo user prompt immediately to Feed (0ms delay)
        feed.write(f"\n[bold green]❯ [white]You:[/white] {prompt}[/bold green]")
        feed.write("[dim]⏳ SuperAgent First Responder evaluating task...[/dim]")

        # 2. Process triage in a background async task so UI never freezes
        asyncio.create_task(self._process_user_task(prompt, proj))

    async def _process_user_task(self, prompt: str, proj: ProjectConfig) -> None:
        feed = self.query_one("#superagent-feed", RichLog)

        # First Responder Triage
        decision = await self.brain.triage(prompt, proj.to_dict())

        # If it's a conversational / query prompt: answer directly in the feed without subprocess!
        if decision.action == "reply":
            feed.write(f"[bold cyan]╭─── [SuperAgent Direct Reply] ──────────────────────────────────[/bold cyan]")
            feed.write(f"[bold cyan]│[/bold cyan] [bold white]{decision.reply_text}[/bold white]")
            feed.write(f"[bold cyan]╰───────────────────────────────────────────────────────────────[/bold cyan]\n")
            return

        # Multi-task or single-task delegation
        tasks = decision.tasks
        if not tasks:
            from superagent.brain import SubtaskItem
            tasks = [SubtaskItem(
                agent=proj.default_agent,
                effort="medium",
                title=prompt[:30],
                task_brief=prompt
            )]

        count = len(tasks)
        feed.write(f"[bold magenta]╭─── [SuperAgent Decision: Spawning {count} Subagent(s)] ────────[/bold magenta]")
        feed.write(f"[bold magenta]│[/bold magenta] 💡 [bold white]Plan:[/bold white] {decision.reasoning or 'Decomposing and launching in background.'}")
        for idx, t in enumerate(tasks, 1):
            feed.write(f"[bold magenta]│[/bold magenta] {idx}. [bold green][{t.agent.upper()}][/bold green] [yellow]({t.effort} effort)[/yellow]: {t.task_brief}")
        feed.write(f"[bold magenta]│[/bold magenta] 🚀 [dim]Status:[/dim] Launching {count} background worker(s)...")
        feed.write(f"[bold magenta]╰───────────────────────────────────────────────────────────────[/bold magenta]\n")

        # Spawn each task concurrently in background!
        for t in tasks:
            task_record = self.runner.create_task(
                agent=t.agent,
                task_desc=t.task_brief,
                project_name=proj.name,
                cwd=proj.path,
                effort=t.effort
            )
            self.select_task(task_record.task_id)
            asyncio.create_task(self.runner.execute_task(task_record))

    async def _handle_command(self, cmd_line: str) -> None:
        feed = self.query_one("#superagent-feed", RichLog)
        parts = cmd_line.strip().split()
        cmd = parts[0].lower()

        if cmd in (":p", ":project", ":projects"):
            if len(parts) == 1:
                # List projects
                feed.write("\n[bold cyan]Registered Projects:[/bold cyan]")
                for p in self.cfg.list_projects():
                    active_marker = "★ (active)" if self.cfg.active_project and self.cfg.active_project.name == p.name else " "
                    feed.write(f"  {active_marker} [bold white]{p.name}[/bold white] -> {p.path} [dim](default agent: {p.default_agent})[/dim]")
                feed.write("[dim]Tip: Switch with :p <name> or press Ctrl+P[/dim]\n")
            else:
                target = parts[1]
                if self.cfg.set_active_project(target):
                    feed.write(f"[bold green]Switched active project to:[/bold green] {target}")
                    self.update_banner()
                else:
                    feed.write(f"[bold red]Project '{target}' not found. Use :projects to list.[/bold red]")

        elif cmd == ":add" and len(parts) >= 3:
            name = parts[1]
            path = parts[2]
            proj = self.cfg.add_project(name, path)
            feed.write(f"[bold green]Added project '{name}' at {proj.path} [agent: {proj.default_agent}][/bold green]")
            self.update_banner()

        elif cmd == ":cancel":
            tid = parts[1] if len(parts) > 1 else self.active_task_id
            if tid and self.runner.cancel_task(tid):
                feed.write(f"[bold magenta]Task #{tid} cancelled.[/bold magenta]")
            else:
                feed.write("[yellow]No active task to cancel.[/yellow]")

        elif cmd in (":key", ":apikey") and len(parts) >= 2:
            key = parts[1]
            self.cfg.set_gemini_api_key(key)
            ok = self.brain.set_api_key(key)
            if ok:
                feed.write("[bold green]✔ Gemini API Key saved and verified! Gemini 2.5 Flash is now active as your First Responder.[/bold green]\n")
            else:
                feed.write(f"[bold green]✔ Gemini API Key saved into ~/.superagent/config.json.[/bold green]\n")

        elif cmd in (":fr", ":first-responder") and len(parts) >= 2:
            agent = parts[1].lower()
            if agent in ("codex", "agy", "antigravity"):
                norm = "agy" if agent in ("agy", "antigravity") else "codex"
                self.cfg.set_first_responder(norm)
                self.brain.set_first_responder(norm)
                feed.write(f"[bold green]✔ First Responder switched to {norm.upper()} CLI (Subscription Mode, Low Effort).[/bold green]\n")
            else:
                feed.write("[yellow]Usage: :fr codex  OR  :fr agy[/yellow]\n")

        elif cmd == ":clear":
            self.action_clear_finished()

        elif cmd == ":help":
            feed.write("\n[bold cyan]SuperAgent Commands:[/bold cyan]")
            feed.write("  @codex <task>      - Delegate task to OpenAI Codex CLI")
            feed.write("  @agy <task>        - Delegate task to Google Antigravity CLI")
            feed.write("  @claude <task>     - Delegate task to Anthropic Claude Code")
            feed.write("  :key <api-key>     - Set Gemini API key for integrated brain")
            feed.write("  :projects          - List all configured projects")
            feed.write("  :p <name>          - Switch active project (or Ctrl+P)")
            feed.write("  :add <name> <path> - Register a new project repository")
            feed.write("  :cancel [id]       - Cancel task (or Ctrl+K)")
            feed.write("  :clear             - Remove finished/failed cards (or Ctrl+L)")
            feed.write("  Ctrl+Y             - Copy entire log to clipboard")
            feed.write("  Ctrl+Q             - Exit SuperAgent")
            feed.write("[dim]Note: Conversational prompts answer directly. Coding tasks delegate with adaptive effort.[/dim]\n")
        else:
            feed.write(f"[red]Unknown command: {cmd}. Type :help for commands.[/red]")

    def action_cancel_active(self) -> None:
        if self.active_task_id:
            self.runner.cancel_task(self.active_task_id)

    def action_clear_finished(self) -> None:
        scroll = self.query_one("#cards-scroll", VerticalScroll)
        to_delete = []
        for tid, card in list(self.card_widgets.items()):
            if card.task_record.status in ("DONE", "FAILED", "CANCELLED"):
                card.remove()
                to_delete.append(tid)

        for tid in to_delete:
            del self.card_widgets[tid]

        if not self.card_widgets:
            scroll.mount(Static("[dim]No subagents running. Enter a task below.[/dim]", id="cards-empty-notice"))
            self.active_task_id = None

    def on_unmount(self) -> None:
        for tid in list(self.runner.tasks.keys()):
            self.runner.cancel_task(tid)

    def action_switch_project_dialog(self) -> None:
        # Toggle or cycle through registered projects
        projects = self.cfg.list_projects()
        if not projects:
            return
        curr_idx = 0
        active = self.cfg.active_project
        if active:
            for idx, p in enumerate(projects):
                if p.name == active.name:
                    curr_idx = idx
                    break
        next_proj = projects[(curr_idx + 1) % len(projects)]
        self.cfg.set_active_project(next_proj.name)
        self.update_banner()
        feed = self.query_one("#superagent-feed", RichLog)
        feed.write(f"[bold cyan]Switched active project to:[/bold cyan] {next_proj.name}")
