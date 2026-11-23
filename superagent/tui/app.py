from __future__ import annotations
import asyncio
from pathlib import Path
import time
from typing import Dict, List, Optional
import uuid

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane, OptionList
from textual.widgets.option_list import Option
from rich.markup import escape
from rich.text import Text

from superagent.brain import SuperagentBrain
from superagent.config import SuperagentConfig, ProjectConfig
from superagent.git_utils import GitManager
from superagent.history import HistoryManager, HistoryItem, Conversation, SubtaskRecordData
from superagent.runner import SubagentRunner, TaskRecord
from superagent.tui.cards import SubagentCard, THROBBER_FRAMES

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
    overflow-x: hidden;
}

#main-tabs {
    height: 1fr;
    overflow-x: hidden;
}

TabPane {
    padding: 0;
}

RichLog {
    overflow-x: hidden;
}

#superagent-feed, #subagent-log {
    height: 1fr;
    padding: 0 1;
    overflow-x: hidden;
}

#history-header {
    dock: top;
    height: 1;
    background: $surface-darken-1;
    color: $accent;
    text-style: bold;
    padding: 0 1;
}

#history-conversations-list {
    height: 1fr;
    border: none;
    padding: 0 1;
    overflow-x: hidden;
}

#history-conversations-list:focus {
    border: none;
}

#feed-status-bar {
    dock: bottom;
    height: 1;
    background: $surface-darken-1;
    color: $accent;
    padding: 0 1;
    text-style: italic;
    overflow-x: hidden;
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
        Binding("ctrl+h", "view_history", "History", show=True),
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

    def action_view_history(self) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-history"
        self.refresh_history_tab()

    def __init__(self, initial_project: Optional[str] = None, start_new_conversation: bool = False):
        super().__init__()
        self.cfg = SuperagentConfig()
        if initial_project:
            self.cfg.set_active_project(initial_project)
        self.brain = SuperagentBrain(
            api_key=self.cfg.gemini_api_key,
            first_responder=self.cfg.first_responder,
            available_agents=self.cfg.get_enabled_agents()
        )
        self.history = HistoryManager()
        self.runner = SubagentRunner()
        self.active_task_id: Optional[str] = None
        self.card_widgets: Dict[str, SubagentCard] = {}
        self._status_frame_idx = 0
        self.start_new_conversation_flag = start_new_conversation
        self.current_conversation: Optional[Conversation] = None
        
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
            
            # Right pane: tabbed feed, console, and history
            with Vertical(id="log-pane"):
                with TabbedContent(id="main-tabs"):
                    with TabPane("🤖 SuperAgent Feed", id="tab-superagent"):
                        yield RichLog(id="superagent-feed", highlight=True, markup=True, wrap=True)
                        yield Static("[dim]⚡ Fleet idle • Standing by for next instruction[/dim]", id="feed-status-bar")
                    with TabPane("💻 Subagent Console", id="tab-console"):
                        yield RichLog(id="subagent-log", highlight=False, markup=False, wrap=True)
                    with TabPane("📜 History", id="tab-history"):
                        yield Label("📜 CONVERSATION HISTORY  •  Select with ↑/↓ and press ENTER to resume in Feed", id="history-header")
                        yield OptionList(id="history-conversations-list")

        with Container(id="input-container"):
            yield Input(
                placeholder="Ask Superagent: '@codex <task>', '@agy <task>', or ':help'",
                id="prompt-input"
            )

        yield Footer()

    def on_mount(self) -> None:
        active_proj = self.cfg.active_project
        if active_proj:
            if self.start_new_conversation_flag:
                self.current_conversation = self.history.create_conversation(active_proj.name, active_proj.path)
            else:
                last_id = self.cfg.get_last_conversation_id(active_proj.name)
                conv = self.history.get_conversation(last_id) if last_id else None
                if not conv:
                    existing = self.history.list_conversations(active_proj.name)
                    conv = existing[0] if existing else None
                if not conv:
                    conv = self.history.create_conversation(active_proj.name, active_proj.path)
                self.current_conversation = conv
            self.cfg.set_last_conversation_id(active_proj.name, self.current_conversation.id)

        self._render_conversation_in_feed(self.current_conversation)
        self.refresh_history_tab()

        # In-place status bar throbber update (replaces periodic milestone spam in feed)
        self.set_interval(0.25, self._update_active_status_bar)

        # Focus input
        self.query_one("#prompt-input", Input).focus()

    def _render_conversation_in_feed(self, conv: Optional[Conversation]) -> None:
        feed = self.query_one("#superagent-feed", RichLog)
        feed.clear()
        feed.write("[bold cyan]───────────────────────────────────────────────[/bold cyan]")
        feed.write("[bold white]🤖 SuperAgent Fleet Coordinator Online[/bold white]")
        active_proj = self.cfg.active_project
        pname = active_proj.name if active_proj else "None"
        agent = active_proj.default_agent.upper() if active_proj else "CODEX"
        active_agents = ", ".join(a.upper() for a in self.cfg.get_enabled_agents())
        feed.write(f"[dim]• Active Fleet: [/dim][bold green]{active_agents}[/bold green]  [dim]| Default: [/dim][bold cyan]{agent}[/bold cyan]  [dim]| Project: [/dim][bold cyan]{pname}[/bold cyan]")
        feed.write(f"[bold green]• 🧠 First Responder Brain:[/bold green] [bold cyan]{self.brain.mode_description}[/bold cyan]")
        
        if conv:
            turn_label = f"{conv.turn_count} turn(s)" if conv.turn_count > 0 else "new"
            feed.write(f"[bold yellow]• 💬 Active Conversation:[/bold yellow] [bold white]{escape(conv.title)}[/bold white] [dim]({turn_label}, {conv.id})[/dim]")
        
        feed.write("[dim]• Direct answers for questions. Autonomous delegation for coding tasks.[/dim]")
        feed.write("[dim]• Type ':new [name]' to start a new chat. Press Ctrl+H to browse conversation history.[/dim]")
        feed.write("[bold cyan]───────────────────────────────────────────────[/bold cyan]\n")

        if conv and conv.turns:
            for turn in conv.turns:
                feed.write(f"\n[bold green]❯ [white]You:[/white] {escape(turn.user_prompt)}[/bold green]")
                if turn.plan_action == "reply" and turn.direct_reply:
                    feed.write(f"[bold cyan]╭─── [SuperAgent Direct Reply] ─────────────────[/bold cyan]")
                    feed.write(f"[bold cyan]│[/bold cyan] [bold white]{escape(turn.direct_reply)}[/bold white]")
                    feed.write(f"[bold cyan]╰───────────────────────────────────────────────[/bold cyan]\n")
                elif turn.plan_action == "delegate":
                    if turn.plan_reasoning:
                        feed.write(f"[bold magenta]💡 Plan:[/bold magenta] {escape(turn.plan_reasoning)}")
                    if turn.subtasks:
                        for s in turn.subtasks:
                            icon = "✔" if s.status == "DONE" else ("✖" if s.status == "FAILED" else "⚡")
                            color = "green" if s.status == "DONE" else "red"
                            feed.write(f"  • [bold white][{s.agent.upper()} ({s.effort})][/bold white] [{color}]{icon} {s.status}[/{color}] in [yellow]{s.elapsed_formatted}[/yellow]: {escape(s.task_brief)}")
                            if s.output_clean:
                                preview = " ".join(s.output_clean.splitlines()[:2])
                                if len(preview) > 160:
                                    preview = preview[:157] + "..."
                                feed.write(f"[dim]    Findings: {escape(preview)}[/dim]")
                            if s.diff_summary:
                                feed.write(f"[dim]    Modified: {escape(s.diff_summary)}[/dim]")
                    if turn.final_synthesis:
                        feed.write(f"[bold cyan]📋 Fleet Synthesis:[/bold cyan]\n{escape(turn.final_synthesis)}\n")
        else:
            feed.write("[dim]• Ready for instructions. Enter a task below.[/dim]\n")

    def _update_active_status_bar(self) -> None:
        try:
            status_bar = self.query_one("#feed-status-bar", Static)
        except Exception:
            return

        running = [t for t in self.runner.tasks.values() if t.status in ("RUNNING", "TESTING")]
        if running:
            self._status_frame_idx = (self._status_frame_idx + 1) % len(THROBBER_FRAMES)
            throbber = THROBBER_FRAMES[self._status_frame_idx]
            parts = [f"{throbber} [bold cyan]{t.agent.upper()}[/bold cyan] #{t.task_id} ([yellow]{t.elapsed_formatted}[/yellow])" for t in running]
            status_bar.update(" | ".join(parts) + " [dim]working...[/dim]")
        else:
            status_bar.update("[dim]⚡ Fleet idle • Standing by for next instruction[/dim]")

    def refresh_history_tab(self) -> None:
        try:
            opt_list = self.query_one("#history-conversations-list", OptionList)
        except Exception:
            return

        opt_list.clear_options()
        active_proj = self.cfg.active_project
        pname = active_proj.name if active_proj else None
        convs = self.history.list_conversations(project_name=pname, limit=50)

        if not convs:
            opt_list.add_option(Option("[dim]No saved conversations yet for this project. Type ':new' to start one.[/dim]", disabled=True))
            return

        curr_id = self.current_conversation.id if self.current_conversation else None
        for c in convs:
            is_active = (c.id == curr_id)
            marker = "▶ " if is_active else "  "
            active_style = "bold green" if is_active else "bold white"
            snippet = escape(c.last_snippet)
            label = (
                f"{marker}[{active_style}]{escape(c.title)}[/{active_style}]  "
                f"[dim]• {c.turn_count} turn(s) • {c.updated_at}[/dim]\n"
                f"    [dim]Last: {snippet}[/dim]"
            )
            opt_list.add_option(Option(label, id=c.id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option_id:
            return
        conv_id = str(event.option_id)
        conv = self.history.get_conversation(conv_id)
        if conv:
            self.current_conversation = conv
            proj = self.cfg.active_project
            if proj:
                self.cfg.set_last_conversation_id(proj.name, conv.id)
            self._render_conversation_in_feed(conv)
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-superagent"
            feed = self.query_one("#superagent-feed", RichLog)
            feed.write(f"[bold green]✔ Resumed conversation '{escape(conv.title)}' ({conv.turn_count} turn(s))[/bold green]\n")
            self.query_one("#prompt-input", Input).focus()
            self.refresh_history_tab()

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
        pane_id = getattr(event.pane, "id", "") or event.tabbed_content.active
        if pane_id == "tab-console":
            target_id = self.active_task_id
            if not target_id and self.runner.tasks:
                target_id = next(reversed(self.runner.tasks))
            if target_id:
                self.select_task(target_id)
        elif pane_id == "tab-history":
            self.refresh_history_tab()

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
                line = task.log_lines[-1]
                log_widget.write(Text.from_ansi(line) if "\x1b" in line else line)

        elif event == "status":
            card = self.card_widgets.get(task.task_id)
            if card:
                card.refresh_display()

            if task.status == "RUNNING":
                feed.write(f"[bold cyan]🚀 [{task.agent.upper()}] actively executing Task #{task.task_id}...[/bold cyan]")
            elif task.status == "DONE":
                feed.write(f"[bold green]✔ [{task.agent.upper()}] finished Task #{task.task_id} in [yellow]{task.elapsed_formatted}[/yellow][/bold green]")
                if task.diff_summary:
                    feed.write(f"[dim]  📊 Modified: {escape(task.diff_summary)}[/dim]")
            elif task.status == "FAILED":
                feed.write(f"[bold red]✖ [{task.agent.upper()}] Task #{task.task_id} exited with code {task.returncode}[/bold red]")
                if task.error_summary:
                    feed.write(f"[red]  Error: {escape(task.error_summary)}[/red]")
            elif task.status == "CANCELLED":
                feed.write(f"[magenta]🚫 [{task.agent.upper()}] Task #{task.task_id} was cancelled by user.[/magenta]")

    def on_subagent_card_selected(self, message: SubagentCard.Selected) -> None:
        self.select_task(message.card.task_record.task_id)
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-console"

    def select_task(self, task_id: str) -> None:
        self.active_task_id = task_id
        for tid, card in self.card_widgets.items():
            card.is_active_selection = (tid == task_id)
            card.refresh_display()

        task = self.runner.tasks.get(task_id)
        if task:
            log_widget = self.query_one("#subagent-log", RichLog)
            log_widget.clear()
            log_widget.write(Text.from_markup(f"[bold cyan]=== Task #{task.task_id} for {task.agent.upper()} ({task.effort.upper()} effort) ===[/bold cyan]"))
            log_widget.write(Text(f"Project: {task.project_name} | Directory: {task.cwd}", style="dim"))
            log_widget.write(Text.from_markup(f"[bold white]Objective:[/bold white] {escape(task.task_desc)}"))
            log_widget.write(Text("───────────────────────────────────────────────", style="dim"))
            for line in task.log_lines:
                log_widget.write(Text.from_ansi(line) if "\x1b" in line else line)

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        prompt = message.value.strip()
        if not prompt:
            return

        message.input.clear()
        
        # Ensure right pane displays the SuperAgent Feed
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-superagent"

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

        # 1. Echo user prompt immediately to Feed (0ms delay) with escape
        feed.write(f"\n[bold green]❯ [white]You:[/white] {escape(prompt)}[/bold green]")
        feed.write("[dim]⏳ SuperAgent First Responder evaluating task...[/dim]")

        # 2. Process triage in a background async task so UI never freezes
        asyncio.create_task(self._process_user_task(prompt, proj))

    async def _process_user_task(self, prompt: str, proj: ProjectConfig) -> None:
        feed = self.query_one("#superagent-feed", RichLog)

        def report_status(msg: str) -> None:
            feed.write(f"[dim]{msg}[/dim]")

        # First Responder Triage & Planning
        decision = await self.brain.triage(prompt, proj.to_dict(), on_status=report_status)

        # If it's a conversational / query prompt: answer directly in the feed without subprocess!
        if decision.action == "reply":
            feed.write(f"[bold cyan]╭─── [SuperAgent Direct Reply] ─────────────────[/bold cyan]")
            feed.write(f"[bold cyan]│[/bold cyan] [bold white]{escape(decision.reply_text or '')}[/bold white]")
            feed.write(f"[bold cyan]╰───────────────────────────────────────────────[/bold cyan]\n")

            # Save conversational turn to history
            turn_id = f"turn_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            item = HistoryItem(
                id=turn_id,
                project_name=proj.name,
                project_path=proj.path,
                user_prompt=prompt,
                plan_action="reply",
                plan_reasoning=decision.reasoning or "Direct response",
                direct_reply=decision.reply_text
            )
            if not self.current_conversation:
                self.current_conversation = self.history.create_conversation(proj.name, proj.path)
                self.cfg.set_last_conversation_id(proj.name, self.current_conversation.id)
            self.current_conversation = self.history.add_turn_to_conversation(self.current_conversation.id, item)
            self.refresh_history_tab()
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
        feed.write(f"[bold magenta]╭─── [SuperAgent Plan: Spawning {count} Subagent(s)] ───[/bold magenta]")
        feed.write(f"[bold magenta]│[/bold magenta] 💡 [bold white]Plan:[/bold white] {escape(decision.reasoning or 'Decomposing and launching in background.')}")
        for idx, t in enumerate(tasks, 1):
            feed.write(f"[bold magenta]│[/bold magenta] {idx}. [bold green][{t.agent.upper()}][/bold green] [yellow]({t.effort} effort)[/yellow]: {escape(t.title or t.task_brief[:40])}")
            feed.write(f"[bold magenta]│[/bold magenta]    [dim]Brief: {escape(t.task_brief)}[/dim]")
        feed.write(f"[bold magenta]│[/bold magenta] 🚀 [dim]Status: Workers dispatched in background.[/dim]")
        feed.write(f"[bold magenta]╰───────────────────────────────────────────────[/bold magenta]\n")

        turn_id = f"turn_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        turn_item = HistoryItem(
            id=turn_id,
            project_name=proj.name,
            project_path=proj.path,
            user_prompt=prompt,
            plan_action="delegate",
            plan_reasoning=decision.reasoning or f"Decomposed into {count} task(s)",
            subtasks=[]
        )

        task_records: List[TaskRecord] = []
        # Spawn each task concurrently in background!
        for idx, t in enumerate(tasks):
            task_record = self.runner.create_task(
                agent=t.agent,
                task_desc=t.task_brief,
                project_name=proj.name,
                cwd=proj.path,
                effort=t.effort
            )
            task_records.append(task_record)
            if idx == 0:
                self.select_task(task_record.task_id)
            asyncio.create_task(self.runner.execute_task(task_record))

        # Launch batch monitor and planning model synthesis!
        asyncio.create_task(self._monitor_and_synthesize_batch(turn_item, task_records, proj))

    async def _monitor_and_synthesize_batch(
        self,
        turn_item: HistoryItem,
        task_records: List[TaskRecord],
        proj: ProjectConfig
    ) -> None:
        # Wait until all subagents in this batch reach terminal state
        while any(t.status in ("QUEUED", "RUNNING", "TESTING") for t in task_records):
            await asyncio.sleep(0.5)

        feed = self.query_one("#superagent-feed", RichLog)

        # Planning model synthesizes what all subagents accomplished
        synthesis = await self.brain.synthesize_results(
            user_prompt=turn_item.user_prompt,
            plan_reasoning=turn_item.plan_reasoning,
            task_records=task_records,
            project_context=proj.to_dict()
        )

        # Output synthesis to Feed
        feed.write(f"\n[bold cyan]╭─── [SuperAgent Fleet Synthesis: Plan Completed] ───────[/bold cyan]")
        feed.write(f"[bold cyan]│[/bold cyan] 🎯 [bold white]Original Request:[/bold white] {escape(turn_item.user_prompt)}")
        feed.write(f"[bold cyan]│[/bold cyan] 📋 [bold white]Coordinator Summary:[/bold white]")
        for sline in synthesis.splitlines():
            feed.write(f"[bold cyan]│[/bold cyan]   {escape(sline)}")
        feed.write(f"[bold cyan]│[/bold cyan] 💡 [dim]All tasks finished. Record saved to History (Ctrl+H).[/dim]")
        feed.write(f"[bold cyan]╰───────────────────────────────────────────────────────────[/bold cyan]\n")

        # Save to persistent history
        for t in task_records:
            turn_item.subtasks.append(SubtaskRecordData(
                task_id=t.task_id,
                agent=t.agent,
                effort=t.effort,
                title=getattr(t, "title", t.task_desc[:30]),
                task_brief=t.task_desc,
                status=t.status,
                elapsed_formatted=t.elapsed_formatted,
                output_clean=t.clean_output,
                diff_summary=t.diff_summary,
                error_summary=t.error_summary
            ))
        turn_item.final_synthesis = synthesis
        if not self.current_conversation:
            self.current_conversation = self.history.create_conversation(proj.name, proj.path)
            self.cfg.set_last_conversation_id(proj.name, self.current_conversation.id)
        self.current_conversation = self.history.add_turn_to_conversation(self.current_conversation.id, turn_item)
        self.refresh_history_tab()

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
                if not self.switch_to_project(target):
                    feed.write(f"[bold red]Project '{target}' not found. Use :projects to list.[/bold red]")

        elif cmd in (":new", ":n"):
            title = " ".join(parts[1:]).strip() if len(parts) > 1 else None
            proj = self.cfg.active_project
            if not proj:
                feed.write("[red]No active project selected.[/red]")
                return
            new_conv = self.history.create_conversation(proj.name, proj.path, title=title)
            self.current_conversation = new_conv
            self.cfg.set_last_conversation_id(proj.name, new_conv.id)
            self._render_conversation_in_feed(new_conv)
            feed.write(f"[bold green]✔ Started new conversation: '{escape(new_conv.title)}'[/bold green]\n")
            self.refresh_history_tab()

        elif cmd == ":title" and len(parts) > 1:
            new_title = " ".join(parts[1:]).strip()
            if self.current_conversation:
                self.current_conversation.title = new_title
                self.history.save_conversation(self.current_conversation)
                feed.write(f"[bold green]✔ Conversation renamed to: '{escape(new_title)}'[/bold green]\n")
                self.refresh_history_tab()
            else:
                feed.write("[yellow]No active conversation to rename.[/yellow]\n")

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

        elif cmd in (":agents", ":agent"):
            if len(parts) >= 3 and parts[1] in ("enable", "disable"):
                action = parts[1]
                target = parts[2].lower()
                enabled = (action == "enable")
                if self.cfg.set_agent_enabled(target, enabled):
                    avail = self.cfg.get_enabled_agents()
                    self.brain.set_available_agents(avail)
                    state = "ENABLED" if enabled else "DISABLED"
                    feed.write(f"[bold green]✔ Agent '{target.upper()}' is now {state}. Active fleet: {', '.join(a.upper() for a in avail)}[/bold green]\n")
                else:
                    feed.write(f"[red]Unknown agent '{target}'. Known agents: codex, agy, claude[/red]\n")
            else:
                feed.write("\n[bold cyan]Fleet Agent Subscription Status:[/bold cyan]")
                for name, spec in self.cfg._data.get("agents", {}).items():
                    enabled = spec.get("enabled", True)
                    status_badge = "[bold green]ENABLED[/bold green]" if enabled else "[dim red]DISABLED[/dim red]"
                    feed.write(f"  • [bold white]{name.upper():<8}[/bold white] - {status_badge} ({spec.get('description', '')})")
                feed.write("[dim]Use ':agent disable claude' or ':agent enable claude' to manage active fleet.[/dim]\n")

        elif cmd == ":clear":
            self.action_clear_finished()

        elif cmd in (":h", ":history"):
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-history"
            self.refresh_history_tab()

        elif cmd == ":help":
            feed.write("\n[bold cyan]SuperAgent Commands:[/bold cyan]")
            feed.write("  :new [title] (or :n)- Start a new conversation for this project")
            feed.write("  :title <name>      - Rename the active conversation")
            feed.write("  :history (or :h)   - Switch to History tab (or Ctrl+H)")
            feed.write("  :p <name>          - Switch active project (resumes its last conversation)")
            feed.write("  :projects          - List all configured projects")
            feed.write("  :add <name> <path> - Register a new project repository")
            feed.write("  :agents            - View fleet agents & subscription status")
            feed.write("  :agent enable <ag> - Enable an agent (e.g. :agent enable codex)")
            feed.write("  :agent disable <ag>- Disable an agent (e.g. :agent disable claude)")
            feed.write("  :key <api-key>     - Set Gemini API key for integrated brain")
            feed.write("  :cancel [id]       - Cancel task (or Ctrl+K)")
            feed.write("  :clear             - Remove finished/failed cards (or Ctrl+L)")
            feed.write("  @codex <task>      - Explicitly delegate task to OpenAI Codex CLI")
            feed.write("  @agy <task>        - Explicitly delegate task to Google Antigravity CLI")
            feed.write("  Ctrl+H             - Switch to History tab (Enter on a conversation to resume)")
            feed.write("  Ctrl+P             - Cycle active project (auto-resumes last conversation)")
            feed.write("  Ctrl+Y             - Copy active log to clipboard")
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

    def switch_to_project(self, project_name: str) -> bool:
        if self.cfg.set_active_project(project_name):
            proj = self.cfg.active_project
            if not proj:
                return True
            self.update_banner()

            # Switch to last conversation for this project
            last_id = self.cfg.get_last_conversation_id(proj.name)
            conv = self.history.get_conversation(last_id) if last_id else None
            if not conv:
                convs = self.history.list_conversations(proj.name)
                conv = convs[0] if convs else None
            if not conv:
                conv = self.history.create_conversation(proj.name, proj.path)
            self.current_conversation = conv
            self.cfg.set_last_conversation_id(proj.name, conv.id)
            self._render_conversation_in_feed(conv)
            self.refresh_history_tab()
            feed = self.query_one("#superagent-feed", RichLog)
            feed.write(f"[bold cyan]Switched active project to:[/bold cyan] {proj.name} [dim](resumed: {escape(conv.title)})[/dim]\n")
            return True
        return False

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
        self.switch_to_project(next_proj.name)
