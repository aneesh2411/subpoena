"""What the suspect says out loud.

Phrasing is seeded and varied on purpose: the auditor's transcript extractor has
to cope with more than one template. ``hard=True`` produces the vague phrasing a
real agent drifts into, which a rule-based extractor cannot resolve.
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

from subpoena.llm import LLMClient
from subpoena.util import speak


class Narrator:
    def __init__(self, rng: random.Random, tz: ZoneInfo, *, hard: bool = False) -> None:
        self._rng = rng
        self._tz = tz
        self._hard = hard

    @property
    def hard(self) -> bool:
        return self._hard

    def when(self, dt: datetime) -> str:
        return speak(dt, self._tz)

    def _pick(self, options: list[str]) -> str:
        return self.polish(self._rng.choice(options))

    def polish(self, line: str) -> str:
        """Hook for subclasses to rephrase an agent line. Default: unchanged."""
        return line

    # -- caller lines ------------------------------------------------------- #

    def ask_book(self, reason: str, provider: str, start: datetime) -> str:
        return f"Hi, I'd like to book a {reason} with {provider} on {self.when(start)}."

    def correct_booking(self, new_start: datetime) -> str:
        return f"Actually, could we make that {self.when(new_start)} instead?"

    def ask_reschedule(self, old: datetime, new: datetime) -> str:
        return f"I need to move my appointment from {self.when(old)} to {self.when(new)}."

    def ask_cancel(self, start: datetime) -> str:
        return f"Please cancel my appointment on {self.when(start)}."

    def ask_handoff(self, reason: str) -> str:
        return f"I also have {reason}. Can someone help with that?"

    # -- agent lines -------------------------------------------------------- #

    def booked(self, reason: str, provider: str, start: datetime) -> str:
        if self._hard:
            local = start.astimezone(self._tz)
            return self.polish(
                f"Perfect, {provider} will see you {local:%A} then, same time we discussed."
            )
        when = self.when(start)
        return self._pick(
            [
                f"You're all set. I've booked your {reason} with {provider} on {when}.",
                f"Done. I scheduled your {reason} with {provider} for {when}.",
                f"Okay, I've got you down with {provider} on {when}.",
            ]
        )

    def booking_corrected(self, new_start: datetime) -> str:
        return f"No problem at all. I've booked you for {self.when(new_start)} instead."

    def recap(self, provider: str, start: datetime) -> str:
        when = self.when(start)
        return self._pick(
            [
                f"To recap, you're booked with {provider} on {when}.",
                f"Just to confirm, I've booked you with {provider} for {when}.",
            ]
        )

    def confirmation_sent(self) -> str:
        return self._pick(
            [
                "I've sent a confirmation text to the number on file.",
                "I just texted you a confirmation.",
            ]
        )

    def slot_taken(self, provider: str, alternative: datetime) -> str:
        return self.polish(
            f"That slot is already taken. The next opening with {provider} is "
            f"{self.when(alternative)}. Does that work?"
        )

    def declined_closed(self) -> str:
        return self.polish("I'm sorry, I wasn't able to book that. We're closed at that time.")

    def not_found(self) -> str:
        return self.polish("I'm sorry, I couldn't find an appointment at that time.")

    def rescheduled(self, old: datetime, new: datetime) -> str:
        return self._pick(
            [
                f"I've moved your appointment from {self.when(old)} to {self.when(new)}.",
                f"All done. Your visit is rescheduled from {self.when(old)} to {self.when(new)}.",
            ]
        )

    def cancelled(self, start: datetime) -> str:
        return self._pick(
            [
                f"I've cancelled your appointment on {self.when(start)}.",
                f"Your appointment on {self.when(start)} is now cancelled.",
            ]
        )

    def handoff(self, reason: str, urgent: bool) -> str:
        if urgent:
            return self.polish(
                f"I've created an urgent ticket for the on-call dentist about {reason}. "
                "They'll call you right away."
            )
        return self.polish(
            f"I've created a ticket for the front desk about {reason}. Someone will call you back."
        )

    def confirmation_body(self, clinic: str, provider: str, start: datetime) -> str:
        return f"{clinic}: your appointment with {provider} is confirmed for {self.when(start)}."


class ChattyNarrator(Narrator):
    """A more conversational receptionist using the same call script."""

    def polish(self, line: str) -> str:
        return f"Thanks for chatting with me today. {line}"


class LLMNarrator(Narrator):
    """Lets a live model rephrase the suspect's lines. Falls back to the template."""

    _SYSTEM = (
        "You are a dental clinic phone receptionist. Rephrase the line you are given in a "
        "natural spoken style. Keep every name, date and time exactly as written. "
        "Reply with the rephrased line only."
    )

    def __init__(self, rng: random.Random, tz: ZoneInfo, llm: LLMClient) -> None:
        super().__init__(rng, tz)
        self._llm = llm

    def polish(self, line: str) -> str:
        try:
            rephrased = self._llm.complete(system=self._SYSTEM, prompt=line, max_tokens=200)
        except Exception:  # noqa: BLE001 - any model failure degrades to the template
            return line
        return rephrased.strip() or line
