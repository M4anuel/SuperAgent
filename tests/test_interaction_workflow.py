from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from textual.widgets import Button, Input, OptionList, RichLog, Static, TabbedContent
from textual.widgets.option_list import Option

from superagent.brain import TriageDecision
from superagent.tui.app import PromptInput, SuperagentApp
from superagent.tui.bridge import InteractionBridge, PendingQuestion
from superagent.tui.cards import SubagentCard
from superagent.tui.interaction import TaskInteractionView, TaskSelector


@pytest.mark.asyncio
async def test_visible_awaiting_input_status_and_card_display():
    app = SuperagentApp()
    async with app.run_test(size=(120, 40)) as pilot:
        t1 = app.runner.create_task(agent="codex", task_desc="Setup DB", project_name="test", cwd=".", effort="medium")
        app._handle_runner_event_sync(t1, "new")
        await pilot.pause()

        card = app.card_widgets[t1.task_id]
        assert not card.is_awaiting_input

        # Ask a question for t1
        pq = app.ask_question(t1.task_id, "Which database engine?", ["SQLite", "PostgreSQL"])
        await pilot.pause()

        assert card.is_awaiting_input is True
        assert card.pending_question == pq
        assert "AWAITING INPUT" in str(card.render()) or "❓" in str(card.render())

        # Status bar reflects awaiting input
        status_bar = app.query_one("#feed-status-bar", Static)
        assert "awaiting input" in str(status_bar.render()).lower()
        assert t1.task_id in str(status_bar.render())

        # Interaction view reflects awaiting input
        status_widget = app.query_one("#awaiting-input-status", Static)
        assert "AWAITING INPUT" in str(status_widget.render())

        # Task selector displays t1 with question snippet
        selector = app.query_one("#task-selector", TaskSelector)
        assert len(selector._task_ids) >= 1
        assert t1.task_id in selector._task_ids


@pytest.mark.asyncio
async def test_multiple_pending_questions_independently_selectable():
    app = SuperagentApp()
    async with app.run_test(size=(120, 40)) as pilot:
        t1 = app.runner.create_task(agent="codex", task_desc="DB setup", project_name="test", cwd=".", effort="medium")
        t2 = app.runner.create_task(agent="agy", task_desc="Frontend styling", project_name="test", cwd=".", effort="low")
        app._handle_runner_event_sync(t1, "new")
        app._handle_runner_event_sync(t2, "new")
        await pilot.pause()

        # Both tasks have distinct questions
        app.ask_question(t1.task_id, "Use SQLite or PostgreSQL?", ["SQLite", "PostgreSQL"])
        app.ask_question(t2.task_id, "Enable Tailwind dark mode?", ["Yes", "No", "System"])
        await pilot.pause()

        selector = app.query_one("#task-selector", TaskSelector)
        question_static = app.query_one("#task-question", Static)
        options_list = app.query_one("#task-options", OptionList)

        # Verify both tasks exist in selector
        assert t1.task_id in selector._task_ids
        assert t2.task_id in selector._task_ids

        # Select t1 in selector
        selector.post_message(TaskSelector.TaskSelected(t1.task_id))
        await pilot.pause()

        assert "SQLite or PostgreSQL" in str(question_static.render())
        option_texts = [str(opt.prompt) for opt in options_list._options]
        assert any("SQLite" in o for o in option_texts)
        assert any("PostgreSQL" in o for o in option_texts)
        assert not any("Tailwind" in o for o in option_texts)

        # Select t2 in selector
        selector.post_message(TaskSelector.TaskSelected(t2.task_id))
        await pilot.pause()

        assert "Tailwind dark mode" in str(question_static.render())
        option_texts_t2 = [str(opt.prompt) for opt in options_list._options]
        assert any("Yes" in o for o in option_texts_t2)
        assert any("No" in o for o in option_texts_t2)
        assert not any("SQLite" in o for o in option_texts_t2)

        # Switching back to t1 preserves its independent question
        selector.post_message(TaskSelector.TaskSelected(t1.task_id))
        await pilot.pause()
        assert "SQLite or PostgreSQL" in str(question_static.render())


@pytest.mark.asyncio
async def test_answering_targets_selected_task_only_with_choice_and_free_text():
    app = SuperagentApp()
    async with app.run_test(size=(120, 40)) as pilot:
        t1 = app.runner.create_task(agent="codex", task_desc="DB setup", project_name="test", cwd=".", effort="medium")
        t2 = app.runner.create_task(agent="agy", task_desc="UI review", project_name="test", cwd=".", effort="medium")
        app._handle_runner_event_sync(t1, "new")
        app._handle_runner_event_sync(t2, "new")
        await pilot.pause()

        app.ask_question(t1.task_id, "Select DB:", ["SQLite", "Postgres"])
        app.ask_question(t2.task_id, "Review scope:", ["Full", "Minimal"])
        await pilot.pause()

        view = app.query_one("#task-interaction-view", TaskInteractionView)
        selector = app.query_one("#task-selector", TaskSelector)
        card1 = app.card_widgets[t1.task_id]
        card2 = app.card_widgets[t2.task_id]

        assert card1.is_awaiting_input is True
        assert card2.is_awaiting_input is True

        # 1. Select t1 and answer with listed choice via option click/selection
        selector.post_message(TaskSelector.TaskSelected(t1.task_id))
        await pilot.pause()
        assert view.selected_task_id == t1.task_id

        options_list = app.query_one("#task-options", OptionList)
        opt = options_list.get_option_at_index(0)
        options_list.post_message(OptionList.OptionSelected(options_list, opt, 0))
        await pilot.pause()

        # t1 is resolved with choice "SQLite"
        assert not app.bridge.has_pending_question(t1.task_id)
        assert card1.is_awaiting_input is False
        assert any("[USER ANSWER] SQLite" in line for line in t1.log_lines)

        # t2 remains pending and untouched!
        assert app.bridge.has_pending_question(t2.task_id) is True
        assert card2.is_awaiting_input is True
        assert not any("[USER ANSWER]" in line for line in t2.log_lines)

        # 2. Select t2 and answer with free text via interaction-input
        selector.post_message(TaskSelector.TaskSelected(t2.task_id))
        await pilot.pause()
        assert view.selected_task_id == t2.task_id

        input_widget = app.query_one("#interaction-input", Input)
        input_widget.value = "Custom deep audit with a11y checks"
        btn_answer = app.query_one("#btn-submit-answer", Button)
        btn_answer.post_message(Button.Pressed(btn_answer))
        await pilot.pause()

        # t2 is resolved with free text
        assert not app.bridge.has_pending_question(t2.task_id)
        assert card2.is_awaiting_input is False
        assert any("[USER ANSWER] Custom deep audit with a11y checks" in line for line in t2.log_lines)
        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_send_context_targets_selected_task_only():
    app = SuperagentApp()
    async with app.run_test(size=(120, 40)) as pilot:
        t1 = app.runner.create_task(agent="codex", task_desc="Backend API", project_name="test", cwd=".", effort="medium")
        t2 = app.runner.create_task(agent="agy", task_desc="Frontend CSS", project_name="test", cwd=".", effort="medium")
        app._handle_runner_event_sync(t1, "new")
        app._handle_runner_event_sync(t2, "new")
        await pilot.pause()

        view = app.query_one("#task-interaction-view", TaskInteractionView)
        selector = app.query_one("#task-selector", TaskSelector)

        # Select t1 and send context
        selector.post_message(TaskSelector.TaskSelected(t1.task_id))
        await pilot.pause()
        assert view.selected_task_id == t1.task_id

        input_widget = app.query_one("#interaction-input", Input)
        input_widget.value = "Remember to validate JWT expiration in headers"
        btn_context = app.query_one("#btn-send-context", Button)
        btn_context.post_message(Button.Pressed(btn_context))
        await pilot.pause()

        # t1 received targeted context only
        assert any("[USER CONTEXT] Remember to validate JWT expiration in headers" in l for l in t1.log_lines)
        assert "Remember to validate JWT expiration in headers" in getattr(t1, "contexts", [])

        # t2 did NOT receive this context
        assert not any("JWT" in l for l in t2.log_lines)
        assert not hasattr(t2, "contexts") or "Remember to validate JWT expiration in headers" not in t2.contexts


@pytest.mark.asyncio
async def test_cancellation_targets_selected_task_only():
    app = SuperagentApp()
    async with app.run_test(size=(120, 40)) as pilot:
        t1 = app.runner.create_task(agent="codex", task_desc="Task 1", project_name="test", cwd=".", effort="medium")
        t2 = app.runner.create_task(agent="agy", task_desc="Task 2", project_name="test", cwd=".", effort="medium")
        app._handle_runner_event_sync(t1, "new")
        app._handle_runner_event_sync(t2, "new")
        await pilot.pause()

        app.ask_question(t1.task_id, "Question 1?")
        app.ask_question(t2.task_id, "Question 2?")
        await pilot.pause()

        selector = app.query_one("#task-selector", TaskSelector)
        card1 = app.card_widgets[t1.task_id]
        card2 = app.card_widgets[t2.task_id]

        # Select t1 and cancel it via button
        selector.post_message(TaskSelector.TaskSelected(t1.task_id))
        await pilot.pause()

        btn_cancel = app.query_one("#btn-cancel-task", Button)
        btn_cancel.post_message(Button.Pressed(btn_cancel))
        await pilot.pause()

        # t1 is CANCELLED and its question cleared
        assert t1.status == "CANCELLED"
        assert not app.bridge.has_pending_question(t1.task_id)
        assert card1.is_awaiting_input is False

        # t2 remains active and pending
        assert t2.status in ("QUEUED", "RUNNING")
        assert app.bridge.has_pending_question(t2.task_id) is True
        assert card2.is_awaiting_input is True


@pytest.mark.asyncio
async def test_ui_local_bridge_with_mock_coordinator():
    mock_coord = MagicMock()
    mock_coord.answer_question = MagicMock()
    mock_coord.send_task_context = MagicMock()
    mock_coord.cancel_task = MagicMock(return_value=True)

    bridge = InteractionBridge(coordinator=mock_coord)

    # Ask question
    pq = bridge.ask_question("task-123", "Continue?", ["yes", "no"])
    assert bridge.has_pending_question("task-123")
    assert pq.question == "Continue?"

    # Answer question
    bridge.answer_question("task-123", "yes")
    assert not bridge.has_pending_question("task-123")
    mock_coord.answer_question.assert_called_once_with("task-123", "yes")

    # Send context
    bridge.send_context("task-123", "additional note")
    mock_coord.send_task_context.assert_called_once_with("task-123", "additional note")

    # Cancel task
    bridge.cancel_task("task-123")
    mock_coord.cancel_task.assert_called_once_with("task-123")


@pytest.mark.asyncio
async def test_prompt_command_and_option_routing():
    app = SuperagentApp()
    async with app.run_test(size=(120, 40)) as pilot:
        t1 = app.runner.create_task(agent="codex", task_desc="API task", project_name="test", cwd=".", effort="medium")
        app._handle_runner_event_sync(t1, "new")
        app.select_task(t1.task_id)
        await pilot.pause()

        # Send context via :context command
        await app._handle_command(":context use rate limiting middleware")
        await pilot.pause()
        assert any("[USER CONTEXT] use rate limiting middleware" in l for l in t1.log_lines)

        # Ask question and answer via :answer command
        app.ask_question(t1.task_id, "Which auth?", ["JWT", "Session"])
        await pilot.pause()
        assert app.bridge.has_pending_question(t1.task_id) is True

        await app._handle_command(":answer JWT")
        await pilot.pause()
        assert not app.bridge.has_pending_question(t1.task_id)
        assert any("[USER ANSWER] JWT" in l for l in t1.log_lines)
