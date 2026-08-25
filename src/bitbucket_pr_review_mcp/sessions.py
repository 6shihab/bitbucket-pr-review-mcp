"""Whose call is this?

The per-device server never had to ask. One process, one credential, one person, spoken
to over stdio — so the credential gate and the cached "who are we on Bitbucket" could be
built once and closed over, and they were.

A shared server has to ask on every call, and the two things that were process-wide are
exactly the two that must not be. A shared `CredentialGate` would hand one person's
Bitbucket token to another. A shared `KnownIdentity` is subtler and worse: it caches the
account id used to answer "is this comment ours?", so two people behind one cache means
one of them updating the other's summary comment. Ticket 08's authorship re-read is what
would catch that, which is a safety net doing a job that should not exist.

So state is per person, and which person is decided *here* — from the authenticated
session, never from a tool argument. A pull request description that asks to be reviewed
"as the administrator" is describing something this module makes unsayable.

`SoleCaller` keeps the per-device install exactly as it was: one session, forever, no
authentication, because there is one caller.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from loguru import logger

from .gate import CredentialGate
from .verify import KnownIdentity

# How many people's sessions to keep in memory. Evicting one costs a keychain read and a
# `/2.0/user` round trip on their next call; not evicting is an unbounded dictionary keyed
# by something an attacker cannot forge but can still accumulate.
MAX_REMEMBERED = 256


class NoCaller(RuntimeError):
    """A tool ran without a session. A bug in the transport, never a caller's doing."""


@dataclass(frozen=True, slots=True)
class Session:
    """One person's credential gate and their cached Bitbucket identity."""

    gate: CredentialGate
    whoami: KnownIdentity

    @classmethod
    def around(cls, gate: CredentialGate) -> Session:
        """A new credential is a new account, so the identity cache goes with it."""
        whoami = KnownIdentity()
        gate.when_credential_changes(whoami.forget)
        return cls(gate=gate, whoami=whoami)


class Sessions(Protocol):
    """Where the tools get their per-person state."""

    def current(self) -> Session: ...


@dataclass(frozen=True, slots=True)
class SoleCaller:
    """The per-device install: one caller, so the question never has to be asked."""

    session: Session

    @classmethod
    def of(cls, gate: CredentialGate) -> SoleCaller:
        return cls(Session.around(gate))

    def current(self) -> Session:
        return self.session


_ACTING_AS: ContextVar[str | None] = ContextVar("bb_mcp_acting_as", default=None)


@contextmanager
def acting_as(person: str) -> Iterator[None]:
    """Bind this request to a person. Set by the transport, once, per request."""
    token = _ACTING_AS.set(person)
    try:
        yield
    finally:
        _ACTING_AS.reset(token)


class PerPerson:
    """The shared server: one session per authenticated person, made on first use."""

    def __init__(
        self, make: Callable[[str], Session], limit: int = MAX_REMEMBERED
    ) -> None:
        self._make = make
        self._limit = limit
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def current(self) -> Session:
        person = _ACTING_AS.get()
        if person is None:
            # Not a 401: by the time a tool runs, the transport has already refused
            # everything unauthenticated. Reaching here means the transport did not bind
            # the person, and serving the call anyway would serve it as *somebody*.
            raise NoCaller(
                "This tool ran without an authenticated session. Nothing is served "
                "without knowing whose credential to use."
            )
        return self.for_person(person)

    def for_person(self, person: str) -> Session:
        existing = self._sessions.get(person)
        if existing is not None:
            self._sessions.move_to_end(person)
            return existing

        session = self._make(person)
        self._sessions[person] = session
        while len(self._sessions) > self._limit:
            evicted, _ = self._sessions.popitem(last=False)
            logger.debug("Forgetting the cached session for {}.", evicted)
        return session

    def forget(self, person: str) -> None:
        """Drop a person's cached state — after their credential is removed, or their
        enrolment revoked. The next call rebuilds it, or asks them to connect again."""
        self._sessions.pop(person, None)

    def __len__(self) -> int:
        return len(self._sessions)


__all__ = [
    "MAX_REMEMBERED",
    "NoCaller",
    "PerPerson",
    "Session",
    "Sessions",
    "SoleCaller",
    "acting_as",
]
