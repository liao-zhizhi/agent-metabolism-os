"""L7 -- compartmental isolation.

A eukaryotic cell works because its units are separated by membranes. The
mitochondrion does not reach into the nucleus and edit the genome; it exports
ATP and imports pyruvate, through channels that can be counted. Multi-agent
systems usually get this wrong in one specific way: they pass a mutable context
object around and call it "collaboration", then spend months debugging a
corruption that no single agent admits to writing.

So here, the membrane is structural, not advisory:

* ``local_context`` is exposed as a ``tuple``. There is no method on a
  compartment that writes another compartment's memory, and the snapshot a
  caller gets cannot be mutated in place.
* The only cross-compartment path is :meth:`CompartmentManager.send_message`,
  which appends to the recipient's *inbox* -- never its context. The recipient
  decides when to ``receive()``, and the arrival is what it chooses to admit.
* A compartment that fails repeatedly is *lysed*: its memory is discarded and
  it stops routing, while every other compartment keeps its state. Failure is
  contained by construction, which is the entire argument for compartments.

Honest limitation: Python has no access control. ``_context`` is reachable if
someone insists, and no amount of ``__slots__`` changes that. The boundary here
is an immutable public view plus a single private delivery path, and the tests
lock that boundary down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

__all__ = [
    "ALIVE",
    "LYSED",
    "Compartment",
    "CompartmentDestroyed",
    "CompartmentError",
    "CompartmentManager",
    "Message",
    "UnknownCompartment",
]

ALIVE = "ALIVE"
LYSED = "LYSED"

DEFAULT_ANOMALY_THRESHOLD = 3


class CompartmentError(Exception):
    """Base class for routing failures."""


class UnknownCompartment(CompartmentError, KeyError):
    """No compartment is registered under that name."""

    def __str__(self) -> str:  # KeyError's repr adds quotes; this reads better
        return str(self.args[0]) if self.args else "unknown compartment"


class CompartmentDestroyed(CompartmentError):
    """The compartment was lysed and no longer participates."""


@dataclass(frozen=True)
class Message:
    """One unit of traffic across a membrane."""

    seq: int
    sender: str
    recipient: str
    body: str

    def __str__(self) -> str:
        return "{} -> {}: {}".format(self.sender, self.recipient, self.body)


class Compartment:
    """One membrane-bounded unit with its own private memory.

    Constructed by :class:`CompartmentManager`; the manager is the only thing
    that may deliver into the inbox.
    """

    def __init__(self, name: str, manager: "CompartmentManager") -> None:
        self._name = name
        self._manager = manager
        self._context: List[Message] = []
        self._inbox: List[Message] = []
        self._anomalies = 0
        self._status = ALIVE
        self._lysis_reason: Optional[str] = None

    # -- read-only view ---------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> str:
        return self._status

    @property
    def alive(self) -> bool:
        return self._status == ALIVE

    @property
    def local_context(self) -> Tuple[Message, ...]:
        """An immutable snapshot of what this compartment has admitted.

        A tuple, not a list: ``other.local_context.append(...)`` fails with
        ``AttributeError`` rather than silently editing someone else's memory.
        """
        return tuple(self._context)

    @property
    def inbox_depth(self) -> int:
        """Messages that arrived but have not been admitted yet."""
        return len(self._inbox)

    @property
    def consecutive_anomalies(self) -> int:
        return self._anomalies

    @property
    def lysis_reason(self) -> Optional[str]:
        return self._lysis_reason

    # -- outbound: through the membrane, never by direct write ------------

    def send_message(
        self, to_compartment: Union[str, "Compartment"], message: str
    ) -> Message:
        """Send to another compartment's inbox.

        This does **not** touch the recipient's ``local_context``. Only the
        recipient's own :meth:`receive` can do that.
        """
        return self._manager.send_message(self, to_compartment, message)

    # -- inbound: the recipient decides ----------------------------------

    def receive(self) -> Tuple[Message, ...]:
        """Admit everything in the inbox into this compartment's context.

        Returns what was admitted. Called by the owner, on its own schedule.
        """
        self._require_alive("receive")
        admitted = tuple(self._inbox)
        self._inbox.clear()
        self._context.extend(admitted)
        return admitted

    def append(self, note: str) -> Message:
        """Write to *this* compartment's own context. Never another's."""
        self._require_alive("append")
        if not isinstance(note, str) or not note.strip():
            raise ValueError("note must be a non-empty str")
        message = self._manager._next_message(self._name, self._name, note)
        self._context.append(message)
        return message

    # -- health -----------------------------------------------------------

    def record_anomaly(self) -> int:
        """Count a consecutive failure; the manager may lyse on the threshold."""
        self._require_alive("record_anomaly")
        return self._manager.record_anomaly(self._name)

    def record_success(self) -> None:
        """A success breaks the consecutive run, so the counter resets."""
        self._require_alive("record_success")
        self._manager.record_success(self._name)

    # -- internals (manager only) ----------------------------------------

    def _deliver(self, message: Message) -> None:
        """Membrane channel: inbox only. The context is not touched here."""
        self._require_alive("deliver")
        self._inbox.append(message)

    def _record_anomaly(self) -> int:
        self._anomalies += 1
        return self._anomalies

    def _record_success(self) -> None:
        self._anomalies = 0

    def _lyse(self, reason: str) -> None:
        self._status = LYSED
        self._lysis_reason = reason
        self._context.clear()
        self._inbox.clear()

    def _require_alive(self, action: str) -> None:
        if not self.alive:
            raise CompartmentDestroyed(
                "compartment {!r} was lysed ({}); cannot {}".format(
                    self._name, self._lysis_reason, action
                )
            )

    def __repr__(self) -> str:
        return "Compartment({!r}, {}, context={}, inbox={})".format(
            self._name, self._status, len(self._context), len(self._inbox)
        )


class CompartmentManager:
    """Routing table, membrane, and the lysis policy for a set of compartments."""

    def __init__(self, anomaly_threshold: int = DEFAULT_ANOMALY_THRESHOLD) -> None:
        if isinstance(anomaly_threshold, bool) or not isinstance(anomaly_threshold, int):
            raise TypeError("anomaly_threshold must be an int")
        if anomaly_threshold < 1:
            raise ValueError("anomaly_threshold must be at least 1")
        self._threshold = anomaly_threshold
        self._compartments: Dict[str, Compartment] = {}
        self._channel_log: List[Message] = []
        self._seq = 0

    # -- registry ---------------------------------------------------------

    @property
    def anomaly_threshold(self) -> int:
        return self._threshold

    def create(self, name: str) -> Compartment:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("compartment name must be a non-empty str")
        if name in self._compartments:
            raise ValueError("compartment {!r} already exists".format(name))
        compartment = Compartment(name, self)
        self._compartments[name] = compartment
        return compartment

    def get(self, name: str) -> Compartment:
        try:
            return self._compartments[name]
        except KeyError:
            raise UnknownCompartment("no compartment named {!r}".format(name)) from None

    def names(self) -> Tuple[str, ...]:
        return tuple(self._compartments)

    def alive_names(self) -> Tuple[str, ...]:
        return tuple(n for n, c in self._compartments.items() if c.alive)

    @property
    def channel_log(self) -> Tuple[Message, ...]:
        return tuple(self._channel_log)

    # -- routing ----------------------------------------------------------

    def send_message(
        self,
        sender: Union[str, Compartment],
        recipient: Union[str, Compartment],
        body: str,
    ) -> Message:
        """Deliver ``body`` into the recipient's inbox, and nowhere else."""
        from_compartment = self._resolve(sender)
        to_compartment = self._resolve(recipient)
        from_compartment._require_alive("send")
        to_compartment._require_alive("receive")

        if not isinstance(body, str) or not body.strip():
            raise ValueError("message body must be a non-empty str")

        message = self._next_message(from_compartment.name, to_compartment.name, body)
        to_compartment._deliver(message)
        self._channel_log.append(message)
        return message

    def _resolve(self, who: Union[str, Compartment]) -> Compartment:
        if isinstance(who, Compartment):
            keeper = self._compartments.get(who.name)
            if keeper is not who:
                raise UnknownCompartment(
                    "compartment {!r} does not belong to this manager".format(who.name)
                )
            return who
        return self.get(who)

    def _next_message(self, sender: str, recipient: str, body: str) -> Message:
        self._seq += 1
        return Message(seq=self._seq, sender=sender, recipient=recipient, body=body)

    # -- health -----------------------------------------------------------

    def record_anomaly(self, name: str) -> int:
        """Count one consecutive anomaly, lysing at the threshold.

        Returns the current consecutive count (after any lysis).
        """
        compartment = self.get(name)
        compartment._require_alive("record_anomaly")
        count = compartment._record_anomaly()
        if count >= self._threshold:
            self.destroy(name, reason="{} consecutive anomalies".format(count))
        return count

    def record_success(self, name: str) -> None:
        self.get(name)._require_alive("record_success")
        self.get(name)._record_success()

    def destroy(self, name: str, reason: str = "manual lysis") -> Compartment:
        """Discard a compartment's memory and stop routing to it.

        Everything else keeps running: the other compartments keep their
        context, their inboxes, and their ability to talk to each other. That
        is the property being demonstrated.
        """
        compartment = self.get(name)
        if not compartment.alive:
            return compartment
        compartment._lyse(reason)
        return compartment

    # -- reporting --------------------------------------------------------

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        return {
            name: {
                "status": c.status,
                "context": [str(m) for m in c.local_context],
                "inbox": c.inbox_depth,
                "anomalies": c.consecutive_anomalies,
                "lysis_reason": c.lysis_reason,
            }
            for name, c in self._compartments.items()
        }

    def __len__(self) -> int:
        return len(self._compartments)

    def __repr__(self) -> str:
        return "CompartmentManager({}, threshold={})".format(
            ", ".join("{}:{}".format(n, c.status) for n, c in self._compartments.items())
            or "empty",
            self._threshold,
        )
