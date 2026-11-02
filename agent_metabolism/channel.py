"""L4 -- the substrate channel: pass a key, not a payload.

Between two compartments, the expensive thing is not the file, it is the *quote*
of the file. Two agents that want to discuss a 40 KB document normally spend
their context window re-serialising it, and the model pays for every byte in
tokens. Biology solved this with substrate channels: the molecule is handed
over, not described. This module is that idea with a dict.

    channel.put("spec_v1", big_text)   -> "spec_v1"
    membrane message: "substrate:spec_v1"          (13 bytes, not 40,000)
    channel.get("spec_v1")             -> big_text  (the real object)

The membrane (L7) carries the key; the channel carries the payload. Neither
layer needs to know the other's job, and the token cost of a handover stops
depending on the size of what is handed over.

One hazard worth stating rather than hiding
-------------------------------------------
``get`` returns the object *itself*, because the point is to avoid copying (and
because a copy could be more expensive than the transfer it prevents). Two
agents holding the same key therefore hold the same list. Mutation is visible
to both, immediately. That is a shared substrate, not an isolated compartment,
and the distinction is the whole difference between L4 and L7:

* use :meth:`SubstrateChannel.get_snapshot` when the recipient must not be able
  to change what the next reader sees;
* use :meth:`SubstrateChannel.put` with ``copy=True`` when the *sender* must
  not be surprised by later mutation.

The default is the fast, shared behaviour, because a channel that silently
deep-copied everything would quietly reintroduce the cost it exists to remove.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

__all__ = [
    "AgentProxy",
    "ChannelError",
    "MissingSubstrate",
    "SubstrateChannel",
    "SubstrateEntry",
    "SubstrateKeyError",
]


class ChannelError(Exception):
    """Base class for channel failures."""


class MissingSubstrate(ChannelError, KeyError):
    """No entry under that key. The classic stale-handle failure."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "missing substrate"


class SubstrateKeyError(ChannelError):
    """A key was malformed, duplicated, or already released."""


@dataclass(frozen=True)
class SubstrateEntry:
    """Metadata for one live entry. Never the payload itself."""

    key: str
    kind: str
    size_bytes: int
    announced_bytes: int  # what the key cost on the wire
    references: int


def _size_bytes(data: Any) -> int:
    """A best-effort size, for reporting only.

    Deliberately not ``len(pickle.dumps(data))``: that would serialise the
    payload to measure it, spending the exact cost the channel exists to avoid.
    """
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    if isinstance(data, str):
        return len(data.encode("utf-8"))
    try:
        return sys.getsizeof(data)
    except TypeError:  # pragma: no cover - objects without a size
        return 0


class SubstrateChannel:
    """An in-memory pool of payloads, addressed by short keys."""

    def __init__(self, *, prefix: str = "substrate") -> None:
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("prefix must be a non-empty str")
        self._prefix = prefix
        self._pool: Dict[str, Any] = {}
        self._sizes: Dict[str, int] = {}
        self._hits = 0
        self._misses = 0
        self._collisions = 0
        self._seq = 0
        self._released: List[str] = []

    # -- the pool ---------------------------------------------------------

    @property
    def shared_memory_pool(self) -> Dict[str, Any]:
        """The pool itself, for inspection.

        Returns the live dict, not a copy, so that "what is in the pool" is
        answerable without duplicating every payload. Treat it as read-only;
        :meth:`put` and :meth:`release` are the mutators.
        """
        return self._pool

    def __len__(self) -> int:
        return len(self._pool)

    def __contains__(self, key: object) -> bool:
        return key in self._pool

    def __iter__(self) -> Iterator[str]:
        return iter(self._pool)

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(self._pool)

    def put(self, key: Optional[str] = None, data: Any = None, *, copy_data: bool = False) -> str:
        """Store ``data`` and return its key.

        ``key`` is generated when omitted. Re-using an existing key is refused
        rather than overwriting: an in-place overwrite would silently change
        what every agent holding that key is about to read, which is exactly the
        invisible-corruption failure the key indirection is meant to prevent.
        """
        if copy_data:
            data = copy.deepcopy(data)

        if key is None:
            resolved = self._generate_key()
        else:
            if not isinstance(key, str) or not key.strip():
                raise SubstrateKeyError("key must be a non-empty str")
            if key in self._pool:
                self._collisions += 1
                raise SubstrateKeyError(
                    "key {!r} is already live; release it or use a new key "
                    "(overwriting would change what existing holders read)".format(key)
                )
            resolved = key

        self._pool[resolved] = data
        self._sizes[resolved] = _size_bytes(data)
        return resolved

    def get(self, key: str) -> Any:
        """Return the live payload. Raises :class:`MissingSubstrate` if absent."""
        if key not in self._pool:
            self._misses += 1
            raise MissingSubstrate(
                "no substrate under {!r}; it was never put, or it was released"
                .format(key)
            )
        self._hits += 1
        return self._pool[key]

    def get_snapshot(self, key: str) -> Any:
        """Return a deep copy, so the caller cannot mutate what others read."""
        return copy.deepcopy(self.get(key))

    def peek_size(self, key: str) -> int:
        """Payload size in bytes, without materialising the payload."""
        if key not in self._sizes:
            raise MissingSubstrate("no substrate under {!r}".format(key))
        return self._sizes[key]

    def release(self, key: str) -> int:
        """Drop an entry and return the bytes reclaimed.

        Unknown keys are not an error: releasing something already released is
        what a cleanup path does on its second pass, and failing there would
        punish idempotence.
        """
        if key not in self._pool:
            return 0
        size = self._sizes.pop(key, 0)
        del self._pool[key]
        self._released.append(key)
        return size

    def clear(self) -> int:
        freed = sum(self._sizes.values())
        self._pool.clear()
        self._sizes.clear()
        return freed

    def stats(self) -> Dict[str, Any]:
        return {
            "entries": len(self._pool),
            "bytes_held": sum(self._sizes.values()),
            "hits": self._hits,
            "misses": self._misses,
            "collisions": self._collisions,
            "released": len(self._released),
        }

    def entries(self) -> Tuple[SubstrateEntry, ...]:
        return tuple(
            SubstrateEntry(
                key=key,
                kind=type(self._pool[key]).__name__,
                size_bytes=size,
                announced_bytes=len(("substrate:" + key).encode("utf-8")),
                references=1,
            )
            for key, size in self._sizes.items()
        )

    def _generate_key(self) -> str:
        while True:
            self._seq += 1
            candidate = "{}_{}".format(self._prefix, self._seq)
            if candidate not in self._pool:
                return candidate

    def __repr__(self) -> str:
        return "SubstrateChannel(entries={}, bytes={})".format(
            len(self._pool), sum(self._sizes.values())
        )


class AgentProxy:
    """An agent's handle on the channel: it trades keys, never payloads.

    Optionally bound to a compartment, in which case a handover also crosses the
    membrane as a *short message*. That composition is the point: L7 decides
    who may talk, L4 decides what the talking costs.
    """

    KEY_PREFIX = "substrate:"

    def __init__(
        self,
        name: str,
        channel: SubstrateChannel,
        compartment: Optional[Any] = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty str")
        if not isinstance(channel, SubstrateChannel):
            raise TypeError("channel must be a SubstrateChannel")
        if compartment is not None and getattr(compartment, "name", None) is None:
            raise TypeError("compartment must expose a .name")
        self._name = name
        self._channel = channel
        self._compartment = compartment
        self._announced_bytes = 0
        self._received: List[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def channel(self) -> SubstrateChannel:
        return self._channel

    @property
    def compartment(self) -> Optional[Any]:
        return self._compartment

    @property
    def announced_bytes(self) -> int:
        """Total bytes this agent put on the wire for handovers."""
        return self._announced_bytes

    # -- publishing -------------------------------------------------------

    def publish(self, data: Any, key: Optional[str] = None, *, copy_data: bool = False) -> str:
        """Deposit a payload and get its key back."""
        return self._channel.put(key=key, data=data, copy_data=copy_data)

    def announce(self, key: str) -> str:
        """The exact string a handover costs. Kept short by construction."""
        body = self.KEY_PREFIX + key
        self._announced_bytes += len(body.encode("utf-8"))
        return body

    def send_substrate(
        self,
        recipient: "AgentProxy",
        key: str,
        *,
        note: str = "",
    ) -> str:
        """Hand ``key`` to ``recipient``, across the membrane when bound.

        The recipient does **not** receive the payload. It receives a reference
        and must call :meth:`receive_substrate` to resolve it -- which is the
        difference between a substrate channel and a copy.

        Returns the wire body, so the caller can see how small it was.
        """
        if not isinstance(recipient, AgentProxy):
            raise TypeError("recipient must be an AgentProxy")
        if key not in self._channel:
            raise MissingSubstrate(
                "cannot hand over {!r}: not in the channel".format(key)
            )
        body = self.announce(key)
        if note:
            body = "{}; {}".format(body, note)

        if recipient._compartment is not None and self._compartment is not None:
            self._compartment.send_message(recipient._compartment, body)
        return body

    def receive_substrate(self, key: str, *, snapshot: bool = False) -> Any:
        """Resolve a key into the payload.

        ``snapshot=True`` deep-copies, for a recipient that must not be able to
        mutate what the next reader will see.
        """
        self._received.append(key)
        if snapshot:
            return self._channel.get_snapshot(key)
        return self._channel.get(key)

    def receive_pending(self) -> Tuple[str, ...]:
        """Admit the inbox in the compartment and return the keys it carried.

        Reaches across L7 deliberately: the compartment is this agent's own, so
        :meth:`Compartment.receive` is the owner acting on its own memory, not
        one agent editing another's.
        """
        if self._compartment is None:
            return ()
        admitted = self._compartment.receive()
        keys = []
        for message in admitted:
            body = message.body
            if body.startswith(self.KEY_PREFIX):
                key = body[len(self.KEY_PREFIX):].split(";", 1)[0].strip()
                keys.append(key)
        return tuple(keys)

    def __repr__(self) -> str:
        return "AgentProxy({!r}, wired={})".format(
            self._name, self._compartment is not None
        )
