"""The interface to an agent: promise first, execute second, answer to the audit.

An agent SDK that only hands out tools is a library, not an interface. The
distinction is where the constraint lives. If "did this tool call succeed?" is
answered by the agent, the answer is worth what the agent's incentives are worth;
so the constraint has to be outside it, and the agent has to cross something to
get work done.

**Promise before execution.** ``execute_tool_with_warrant`` requires an
``expected_artifact_hash``, and registers it with the membrane *before* the tool
runs. The agent therefore has to commit to what success looks like while it still
cannot know whether it will succeed. That ordering is the potential difference.
If the promise were checked afterwards against a hash the agent supplied at the
end, the agent would be setting its own acceptance criterion and there would be
nothing to cross.

**The membrane decides.** The expected hash is registered with the injected
membrane, the tool runs, the produced artefact is hashed, and the digest is then
offered to the membrane for a warrant. A mismatch is caught by the membrane's own
recomputation -- not by a comparison this class performs and could be persuaded
to skip. Every grant, denial and verdict goes through the membrane, so the log
has one author.

**A note on ordering that cannot be avoided.** The tool must run before its
output can be hashed; that is physics, not policy. What the design guarantees is
that the *expectation* precedes the act. The gap between them -- the tool runs
and then turns out not to match -- is exactly what the FAIL record and the
``ArtefactMismatchError`` are for.

Zero dependencies. The membrane and its ledger are injected and duck-typed; this
package does not import the auditor, and an object satisfying the small protocol
below is enough. A missing membrane fails closed: with nothing to cross, nothing
is granted.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable, Dict, Mapping, Optional

__all__ = [
    "AgentWarrantError",
    "ArtefactMismatchError",
    "MembraneAgent",
    "MissingPromiseError",
    "ToolOutcome",
]

_HEX = set("0123456789abcdef")


class AgentWarrantError(Exception):
    """Base class: the agent was not allowed to do the thing."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def as_dict(self) -> Dict[str, str]:
        return {"reason": self.reason, "message": self.message}


class MissingPromiseError(AgentWarrantError):
    """No expected hash was declared, so there was nothing to agree to."""


class ArtefactMismatchError(AgentWarrantError):
    """The tool ran, and what it produced is not what was promised."""


class ToolOutcome:
    """What a tool returned: a value, and the artefact it wrote (if any)."""

    __slots__ = ("value", "artefact_path")

    def __init__(self, value: Any = None, artefact_path: Optional[str] = None) -> None:
        self.value = value
        self.artefact_path = artefact_path

    def __repr__(self) -> str:
        return "ToolOutcome(value={!r}, artefact_path={!r})".format(
            self.value, self.artefact_path
        )


class MembraneAgent:
    """An agent whose tool calls have to cross a membrane.

    Args:
        membrane: the gate. Must expose ``register_digest(capability, digest)``,
            ``request_action_warrant(payload, capability)``, ``redeem(warrant)``
            and ``append_call(...)``. In practice this is
            ``audit_sidecar.tools.membrane.MembraneGateway``; it is duck-typed
            here so this package can be installed without the auditor.
        name: used in the ledger records.
        tools: ``{tool_name: callable}``. A callable takes the ``args`` mapping
            as keyword arguments and returns either a raw value, a path string,
            or a :class:`ToolOutcome`.
        capability: the capability demanded for every tool call.
        artefact_of: optional ``callable(tool_name, args) -> path`` used when a
            tool writes somewhere its return value does not name.
    """

    def __init__(
        self,
        membrane: Any,
        *,
        name: str = "agent",
        tools: Optional[Mapping[str, Callable[..., Any]]] = None,
        capability: str = "write",
        artefact_of: Optional[Callable[[str, Mapping[str, Any]], str]] = None,
    ) -> None:
        if membrane is None:
            raise ValueError(
                "membrane is required: an agent with nothing to cross has no "
                "interface, only a library"
            )
        for method in ("register_digest", "request_action_warrant", "append_call"):
            if not callable(getattr(membrane, method, None)):
                raise TypeError(
                    "membrane must expose {}(), got {!r}".format(
                        method, type(membrane).__name__
                    )
                )
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty str")

        self._membrane = membrane
        self._name = name
        self._tools: Dict[str, Callable[..., Any]] = dict(tools or {})
        self._capability = capability
        self._artefact_of = artefact_of
        self._calls = 0
        self._denials = 0

    # -- registry ---------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def membrane(self) -> Any:
        return self._membrane

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def denials(self) -> int:
        return self._denials

    def register_tool(self, name: str, function: Callable[..., Any]) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool name must be a non-empty str")
        if not callable(function):
            raise TypeError("tool must be callable")
        self._tools[name] = function

    # -- the gate ---------------------------------------------------------

    def execute_tool_with_warrant(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        expected_artifact_hash: Optional[str] = None,
    ) -> ToolOutcome:
        """Run a tool only if the promise can be agreed to and then met.

        Args:
            tool_name: must be registered.
            args: keyword arguments for the tool.
            expected_artifact_hash: the digest the agent commits to *before*
                running. Required. ``sha256:`` prefix optional, case-insensitive.

        Raises:
            MissingPromiseError: no hash was supplied, or it is not a digest.
                Nothing ran.
            ArtefactMismatchError: the tool produced something else. The tool
                *did* run, so this is not a prevention -- it is a recorded
                failure, and the caller must not treat the artefact as valid.
            AgentWarrantError: the membrane refused the warrant.

        The order is fixed and load-bearing:

        1. validate the promise, 2. register it with the membrane, 3. run the
        tool, 4. hash what appeared, 5. ask the membrane for a warrant, 6. redeem.
        """
        expected = self._normalise_hash(expected_artifact_hash, tool_name)
        if expected is None:
            self._deny(
                tool_name,
                "NO_EXPECTED_HASH",
                "execute_tool_with_warrant requires expected_artifact_hash: an "
                "agent that names its success criterion after the fact is "
                "grading its own work",
            )

        if tool_name not in self._tools:
            self._deny(
                tool_name,
                "UNKNOWN_TOOL",
                "no tool named {!r} is registered on this agent".format(tool_name),
            )

        if not isinstance(args, Mapping):
            self._deny(
                tool_name,
                "MALFORMED_ARGS",
                "args must be a mapping, got {!r}".format(type(args).__name__),
            )

        # 2. The promise, registered before anything runs. This is the potential
        #    difference: from here on the agent cannot change the criterion.
        try:
            self._membrane.register_digest(self._capability, expected)
        except Exception as problem:
            self._deny(
                tool_name,
                "PROMISE_REJECTED",
                "the membrane refused to register the expected hash: {}".format(problem),
            )

        # 3. Execute.
        self._calls += 1
        result = self._tools[tool_name](**args)
        outcome, artefact_path = self._unpack(tool_name, args, result)

        # 4. Hash what actually appeared.
        actual = self._hash_artefact(tool_name, artefact_path)
        if actual is None:
            self._record(
                tool_name,
                "FAIL",
                expected=expected,
                reason="no artefact produced; nothing to witness",
            )
            raise ArtefactMismatchError(
                "ARTEFACT_MISSING",
                "tool {!r} produced no artefact at {!r}; the promise cannot be "
                "met and cannot be checked".format(tool_name, artefact_path),
            )

        if actual != expected:
            # 5a. The promise was not kept. Recorded, and refused.
            self._record(
                tool_name,
                "FAIL",
                expected=expected,
                actual=actual,
                reason="artefact does not match the hash promised before execution",
            )
            raise ArtefactMismatchError(
                "ARTEFACT_MISMATCH",
                "tool {!r} promised {} but produced {}; the artefact is not the "
                "one the agent committed to".format(
                    tool_name, expected[:19], actual[:19]
                ),
            )

        # 5b. Ask the membrane for the warrant. The membrane recomputes from the
        #     file itself rather than trusting ``actual`` -- that independence is
        #     why the check is not simply the comparison three lines above.
        try:
            warrant = self._membrane.request_action_warrant(
                {"kind": "file", "path": artefact_path, "digest": actual},
                self._capability,
            )
        except Exception as problem:
            self._deny(
                tool_name,
                getattr(problem, "reason", "WARRANT_REFUSED"),
                "the membrane refused the warrant: {}".format(problem),
            )

        redeemer = getattr(self._membrane, "redeem", None)
        if callable(redeemer):
            try:
                redeemer(warrant)
            except Exception as problem:
                self._deny(
                    tool_name,
                    getattr(problem, "reason", "WARRANT_REFUSED"),
                    "the membrane refused to redeem the warrant: {}".format(problem),
                )

        self._record(
            tool_name,
            "PASS",
            expected=expected,
            actual=actual,
            capability=self._capability,
        )
        return outcome

    # -- helpers ----------------------------------------------------------

    def _deny(self, tool_name: str, reason: str, message: str):
        """Record the refusal, then raise. Never returns."""
        self._denials += 1
        self._record(tool_name, "FAIL", expected=None, reason="{}: {}".format(reason, message))
        if reason == "NO_EXPECTED_HASH":
            raise MissingPromiseError(reason, message)
        raise AgentWarrantError(reason, message)

    def _record(self, tool_name: str, verdict: str, *, expected, reason=None, **extra):
        """All logging goes through the membrane, so the log has one author."""
        payload: Dict[str, Any] = {"agent": self._name}
        if expected is not None:
            payload["expected_digest"] = expected
        if reason is not None:
            payload["reason"] = reason
        payload.update(extra)
        # ``tool`` and ``verdict`` are positional on ``append_call``; leaving
        # them in ``extra`` would raise a duplicate-keyword TypeError and the
        # fallback below would silently drop every field this method exists to
        # record. They are stripped here rather than passed twice.
        try:
            self._membrane.append_call(tool_name, verdict, **payload)
        except TypeError:
            # A membrane with the narrower signature still satisfies the
            # contract; the verdict field is the part that must not be lost, and
            # the remaining fields ride along in one extra mapping so a reader
            # does not lose the promise it was checked against.
            self._membrane.append_call(tool_name, verdict, extra=payload)
        except Exception:  # pragma: no cover - a broken log must not mask the refusal
            pass

    def _unpack(self, tool_name: str, args: Mapping[str, Any], result: Any):
        """Return ``(outcome, artefact_path)`` for whatever the tool returned."""
        if isinstance(result, ToolOutcome):
            return result, result.artefact_path
        if isinstance(result, str) and os.path.exists(result):
            return ToolOutcome(value=result, artefact_path=result), result
        path = None
        if self._artefact_of is not None:
            path = self._artefact_of(tool_name, args)
        elif isinstance(args, Mapping):
            candidate = args.get("path") or args.get("artefact_path")
            if isinstance(candidate, str):
                path = candidate
        return ToolOutcome(value=result, artefact_path=path), path

    def _hash_artefact(self, tool_name: str, path: Optional[str]) -> Optional[str]:
        if not isinstance(path, str) or not path:
            return None
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def _normalise_hash(value: Any, tool_name: str) -> Optional[str]:
        """A ``sha256:<hex>`` string, or ``None`` when it is not one."""
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        text = value.strip().lower()
        if text.startswith("sha256:"):
            text = text[len("sha256:"):]
        if len(text) != 64 or any(ch not in _HEX for ch in text):
            return None
        return "sha256:" + text

    def __repr__(self) -> str:
        return "MembraneAgent({!r}, tools={}, calls={}, denials={})".format(
            self._name, sorted(self._tools), self._calls, self._denials
        )
