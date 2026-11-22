"""Progress reporting for long jobs (user rule 2026-10-02: every long task says how far
along it is in real numbers, e.g. "Writing page 3 of 10", never just "Working…").

A job's ``progress(done, total, stage="")`` callback returns False to cancel:
``done`` items are finished (so item done + 1 is being worked on) out of ``total``;
``stage`` names the step and its unit when a job has several ("Reading page",
"Writing page", "Verifying check"), so the UI can say "Reading page 3 of 10".
``total`` 0 means a step that can't be counted (``stage`` then says what it is).
Callbacks written for (done, total) still work: ``report`` passes the stage only when
there is one.
"""

from __future__ import annotations

from collections.abc import Callable

Progress = Callable[..., bool]


def report(progress: Progress | None, done: int, total: int, stage: str = "") -> bool:
    """Tell ``progress`` (if any); False when the user asked to cancel."""
    if progress is None:
        return True
    keep_going = progress(done, total, stage) if stage else progress(done, total)
    return keep_going is not False


def stage_text(stage: str, done: int, total: int) -> str:
    """ "Reading page 3 of 10" (item done + 1), or just the stage when it can't be counted."""
    if total <= 0:
        return stage
    return f"{stage} {min(done + 1, total)} of {total}"
