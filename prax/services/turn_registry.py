"""Running turns, so a person can stop one.

A turn used to have no handle: once the orchestrator started, the only ways it
ended were finishing, the wall-clock cap, or a process restart. So when a turn
went wrong — a browser loop that kept re-typing the same code for 26 minutes —
a "stop" from the user could not reach it. Worse, "stop" arrived as a new turn
that knew nothing of the running one, and was read as a request about
something else entirely.

Each orchestrator turn registers here with the event its governed tools check
before every call (:class:`TurnCancelled` is raised from the next tool call,
in the hub or any spoke). :func:`cancel` sets the event; the turn unwinds and
reports that it stopped.

``TurnCancelled`` derives from ``BaseException`` on purpose: spoke runners,
tool wrappers and retry loops all catch ``Exception`` to turn failures into
text the model can react to — which is exactly what must not happen to a stop.
"""
from __future__ import annotations

import itertools
import re
import threading
import time
from dataclasses import dataclass, field


class TurnCancelled(BaseException):  # noqa: N818 - reads as an event, like KeyboardInterrupt
    """Raised inside a turn that a person asked to stop."""


@dataclass
class Turn:
    id: str
    user_id: str
    trigger: str
    source: str = ""
    started: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    reason: str = ""

    def age_seconds(self) -> float:
        return time.monotonic() - self.started


_lock = threading.Lock()
_turns: dict[str, Turn] = {}
_ids = itertools.count(1)


def begin(user_id: str, trigger: str, source: str = "") -> Turn:
    turn = Turn(id=f"turn-{next(_ids)}", user_id=user_id or "", trigger=trigger or "", source=source or "")
    with _lock:
        _turns[turn.id] = turn
    return turn


def end(turn: Turn | None) -> None:
    if turn is not None:
        with _lock:
            _turns.pop(turn.id, None)


def running(user_id: str, *, exclude: Turn | None = None) -> list[Turn]:
    """This user's turns still in progress, oldest first."""
    with _lock:
        turns = [t for t in _turns.values()
                 if t.user_id == user_id and t is not exclude and not t.cancel.is_set()]
    return sorted(turns, key=lambda t: t.started)


def cancel(user_id: str, *, exclude: Turn | None = None, reason: str = "the user asked to stop") -> list[Turn]:
    """Stop every turn of *user_id* except *exclude*; returns the ones stopped."""
    stopped = running(user_id, exclude=exclude)
    for turn in stopped:
        turn.reason = reason
        turn.cancel.set()
    return stopped


def describe(turn: Turn) -> str:
    minutes = int(turn.age_seconds() // 60)
    age = f"{minutes} min" if minutes else f"{int(turn.age_seconds())} s"
    trigger = " ".join(turn.trigger.split())
    if len(trigger) > 80:
        trigger = trigger[:77] + "..."
    return f'"{trigger}" (running {age})'


# A whole message that only says stop. Anything longer ("stop the schedule",
# "stop sending me X") names what to stop and goes to the model, which is told
# what is running (see running_turns_note) and can stop it with a tool.
_STOP_RE = re.compile(
    r"^\s*(?:please\s+)?(?:stop|cancel|abort|halt|quit|enough|nevermind|never\s+mind|"
    r"stop\s+(?:it|that|this|now|please|working)|cancel\s+(?:it|that|this))"
    r"\s*(?:please|now)?\s*[.!]*\s*$",
    re.IGNORECASE,
)


def is_stop_request(text: str) -> bool:
    return bool(_STOP_RE.match(text or ""))
