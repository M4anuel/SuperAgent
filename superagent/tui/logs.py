"""Log rendering and tail-following policy shared by the feed and console."""
from __future__ import annotations
import re
from typing import Any
from rich.console import RenderableType
from rich.markdown import Markdown
from textual.widgets import RichLog


class ReadingLog(RichLog):
    """Follow output until the reader scrolls up from the end.

    If the user manually scrolls up to read earlier content, incoming writes
    preserve the current scroll position so the user is never yanked back down.
    When the user scrolls back to the bottom, tail-following re-engages smoothly.
    Any string containing markdown tables is automatically converted to Rich Markdown.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._follow_tail = True
        super().__init__(min_width=1, **kwargs)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        # Follow tail if at the bottom or close to it (within 2 rows).
        # If user scrolled further up to read earlier lines, preserve reading position.
        self._follow_tail = (self.max_scroll_y - new_value) <= 2

    def write(
        self,
        content: RenderableType | object,
        width: int | None = None,
        expand: bool = True,
        shrink: bool = True,
        scroll_end: bool | None = None,
        animate: bool = False,
    ) -> ReadingLog:
        # Auto-format markdown tables if plain text with table markdown is provided
        if isinstance(content, str) and "|" in content and re.search(r"\|[ \t]*[-:]+[-| :]*\|", content):
            content = Markdown(content)

        if scroll_end is None:
            scroll_end = self.auto_scroll and self._follow_tail
        super().write(content, width, expand, shrink, False, animate)
        if scroll_end:
            self.call_after_refresh(self._scroll_to_tail, animate)
        return self

    def _scroll_to_tail(self, animate: bool) -> None:
        if self._follow_tail:
            self.scroll_end(animate=animate, immediate=True, x_axis=False)

    def clear(self) -> ReadingLog:
        self._follow_tail = True
        return super().clear()

