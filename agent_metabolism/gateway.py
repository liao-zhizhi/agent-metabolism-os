"""The internal interface: one call that reports all eight laws at once.

The rest of this package exposes each layer as machinery -- an account you
spend from, a scheduler that picks a beat, a manager that lyses compartments.
That is the right shape for a system *doing* the regulation, and the wrong shape
for one *reporting* it. A supervisor, a dashboard, or a second process asking
"is this organism healthy" should not have to reach into five objects and know
which attribute carries which decision.

``MetabolicGateway`` is that single read-only surface. It owns no state and
changes none: constructing it twice over the same objects gives the same answer,
and calling ``get_current_laws_status`` cannot consume energy or advance a
counter.

**L5 is the seam, and this module does not paper over it.** The saturation brake
deliberately lives outside this package, in the immune sidecar, because a brake
that reads the regulated system's own accounting can be tripped by the same
defect that corrupts the accounting. So this gateway does not import the
sidecar -- it accepts any object that answers ``is_inflamed()`` or exposes a
``state`` equal to ``"INFLAMED"``, and reports ``attached: False`` when handed
nothing. A gateway that invented "not inflamed" for an unattached brake would
report the most important safety layer as healthy precisely when it is absent.

Zero dependencies, like the rest of the package: the immune state is duck-typed,
not imported, so installing this package never pulls in the auditor.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .compartment import ALIVE, CompartmentManager
from .energy import EXHAUSTED, EnergyAccount
from .regime import CONSERVE, EMERGENCY, GROW, evaluate_regime
from .scheduler import FAST, SLOW, DualBeatScheduler

__all__ = ["MetabolicGateway", "LAWS"]

#: The eight laws this gateway reports. L5 is listed with
#: ``hosted_here = False`` because the brake is hosted by the immune sidecar;
#: keeping the row marks the seam rather than hiding it.
LAWS = (
    {"law": "L1", "name": "Energy", "question": "what charge remains", "hosted_here": True},
    {"law": "L2", "name": "Beat", "question": "fast or slow thinking", "hosted_here": True},
    {"law": "L3", "name": "Regime", "question": "how much runs", "hosted_here": True},
    {"law": "L4", "name": "Channel", "question": "keys, not payloads", "hosted_here": True},
    {"law": "L5", "name": "Brake", "question": "is the saturation brake tripped", "hosted_here": False},
    {"law": "L6", "name": "Circadian", "question": "has the day been consolidated", "hosted_here": True},
    {"law": "L7", "name": "Compartment", "question": "how many units are alive", "hosted_here": True},
    {"law": "--", "name": "Penalty", "question": "what has the damage cost", "hosted_here": True},
)


class MetabolicGateway:
    """Read-only view over the metabolic layers.

    All arguments are optional so the object can be built for whatever the
    caller actually has. What was *not* supplied is reported as such, per law,
    rather than defaulted to a healthy value.

    Args:
        scheduler: a :class:`DualBeatScheduler`. When given, its account and
            penalty ledger supply L1 and the penalty row.
        account: an :class:`EnergyAccount`. Overrides the scheduler's account
            when both are given, which is the case when a caller keeps the
            account and the scheduler's own copy has drifted.
        compartments: a :class:`CompartmentManager` for L7.
        immune_state: any object answering ``is_inflamed()`` or carrying a
            ``state`` string. Duck-typed on purpose -- see the module docstring.
        last_response: the most recent ``LLMResponse``. L2 can only report an
            *observed* beat when one is supplied; otherwise it reports the beat
            the current charge would permit, and says that it is predicted.
        recent_error_rate, critical_errors: the failure feedback L3 needs. Both
            default to a clean record, which is the documented default of
            :func:`evaluate_regime` rather than an assumption made here.
    """

    def __init__(
        self,
        scheduler: Optional[DualBeatScheduler] = None,
        account: Optional[EnergyAccount] = None,
        compartments: Optional[CompartmentManager] = None,
        immune_state: Any = None,
        last_response: Any = None,
        recent_error_rate: float = 0.0,
        critical_errors: int = 0,
        llm_proxy: Any = None,
        agents: Any = None,
    ) -> None:
        if scheduler is not None and not isinstance(scheduler, DualBeatScheduler):
            raise TypeError("scheduler must be a DualBeatScheduler or None")
        if account is not None and not isinstance(account, EnergyAccount):
            raise TypeError("account must be an EnergyAccount or None")
        if compartments is not None and not isinstance(compartments, CompartmentManager):
            raise TypeError("compartments must be a CompartmentManager or None")
        if llm_proxy is not None and not callable(getattr(llm_proxy, "lock_status", None)):
            raise TypeError(
                "llm_proxy must expose lock_status(), got {!r}".format(
                    type(llm_proxy).__name__
                )
            )

        self._scheduler = scheduler
        self._account = account if account is not None else (
            scheduler.account if scheduler is not None else None
        )
        self._compartments = compartments
        self._immune = immune_state
        self._last_response = last_response
        self._recent_error_rate = recent_error_rate
        self._critical_errors = critical_errors
        self._llm_proxy = llm_proxy
        self._agents = list(agents) if agents is not None else []

    # -- the single public call -------------------------------------------

    def get_current_laws_status(self) -> Dict[str, Any]:
        """Report the state of every law this object can see.

        Returns a dict keyed ``L1_Energy``, ``L2_Beat``, ``L3_Regime``,
        ``L5_Brake``, ``L7_Compartment`` -- the five the interface asks for --
        plus ``L4_Channel``, ``L6_Circadian``, ``Penalty`` and ``_witness``.

        ``_witness`` names, in plain words, what was measured and what was
        missing. A consumer that reads only the five keys will get ``None``
        where nothing was attached, which is the honest answer: this gateway
        has no opinion about a layer it cannot see.
        """
        witness: Dict[str, str] = {}

        energy = self._energy()
        witness["L1_Energy"] = (
            "measured from the energy account" if energy["measured"]
            else "no energy account attached"
        )

        beat = self._beat()
        witness["L2_Beat"] = beat["reason"]

        regime = self._regime(energy)
        witness["L3_Regime"] = (
            "derived from charge, error feedback and penalty state" if regime["measured"]
            else "no energy account attached; regime cannot be derived"
        )

        channel = self._channel()
        witness["L4_Channel"] = channel["reason"]

        brake = self._brake()
        witness["L5_Brake"] = (
            "read from the externally hosted immune state" if brake["attached"]
            else "no immune state attached: L5 is hosted by the auditor, not here"
        )

        circadian = self._circadian()
        witness["L6_Circadian"] = circadian["reason"]

        compartment = self._compartment()
        witness["L7_Compartment"] = (
            "counted from the compartment manager" if compartment["measured"]
            else "no compartment manager attached"
        )

        penalty = self._penalty()
        witness["Penalty"] = penalty["reason"]

        external = self._external()

        return {
            "L1_Energy": energy,
            "L2_Beat": beat,
            "L3_Regime": regime,
            "L4_Channel": channel,
            "L5_Brake": brake,
            "L6_Circadian": circadian,
            "L7_Compartment": compartment,
            "Penalty": penalty,
            "External": external,
            "_witness": witness,
        }

    # -- external interfaces ------------------------------------------------

    def _external(self) -> Dict[str, Any]:
        """The state of the two outbound interfaces, as scalars.

        Reported here so a router has one place to look, and reported as counts
        rather than objects so that having the status of the model proxy does not
        confer the ability to call it.
        """
        proxy = self._llm_proxy
        if proxy is None:
            llm = {"attached": False, "reason": "no LLM proxy attached"}
        else:
            try:
                status = proxy.lock_status()
            except Exception:  # pragma: no cover - a broken proxy is not "fine"
                llm = {"attached": True, "reason": "the proxy's lock status is unreadable"}
            else:
                llm = {
                    "attached": True,
                    "locked": bool(status.get("locked")),
                    "calls": status.get("calls"),
                    "unlocked_calls": status.get("unlocked_calls"),
                    "reason": status.get("reason"),
                }

        agents = []
        for agent in self._agents:
            agents.append(
                {
                    "name": getattr(agent, "name", "unknown"),
                    "calls": getattr(agent, "calls", None),
                    "denials": getattr(agent, "denials", None),
                }
            )
        return {
            "llm_proxy": llm,
            "agents": agents,
            "reason": (
                "both interfaces attached" if proxy is not None and agents
                else "at least one interface is not attached"
            ),
        }

    def routing_view(self) -> Dict[str, Any]:
        """The only thing an external router may read: charge and headroom.

        Deliberately narrow. A router needs to know how much is left and whether
        heavy work is currently allowed; it does not need the ledger, the account
        object, or a way to change either. Everything here is a scalar, and there
        is no setter anywhere on this class -- the surface is read-only because
        there is nothing to write with, not because a caller promised not to.
        """
        energy = self._energy()
        regime = self._regime(energy) if energy["measured"] else {"current": None}
        current = regime.get("current")
        penalty = self._penalty()
        brake = self._brake()
        return {
            "L1_Energy": {
                "current": energy["current"],
                "budget": energy["budget"],
                "ratio": energy["ratio"],
                "state": energy["state"],
                "measured": energy["measured"],
            },
            "L3_Regime": {
                "current": current,
                "measured": regime.get("measured", energy["measured"]),
            },
            "heavy_work_allowed": current == GROW,
            # L3's write gate, reported as a decision rather than left for the
            # caller to re-derive from two vocabularies. It is the same rule the
            # membrane enforces -- a CRITICAL ledger or a tripped brake closes
            # writes -- stated once here so a router and the gate cannot drift.
            "writes_allowed": not (
                penalty.get("penalty_state") == "CRITICAL"
                or brake.get("tripped") is True
            ),
        }

    # -- per-law readers ---------------------------------------------------

    def _energy(self) -> Dict[str, Any]:
        account = self._account
        if account is None:
            return {
                "measured": False,
                "current": None,
                "budget": None,
                "ratio": None,
                "state": None,
                "reason": "no energy account attached",
            }
        return {
            "measured": True,
            "current": account.current_energy,
            "budget": account.budget,
            "ratio": account.ratio,
            "state": account.state,
            "reason": "measured",
        }

    def _beat(self) -> Dict[str, Any]:
        """The beat, observed when possible and predicted otherwise.

        The scheduler picks a beat per call and keeps no memory of the last one,
        so "which beat is running" is only answerable from a response the caller
        supplies. Without one, the useful question is which beat the current
        charge *permits*, and this says so rather than implying an observation.
        """
        observed_mode = getattr(self._last_response, "mode", None)
        if observed_mode in (FAST, SLOW):
            return {
                "measured": True,
                "current": observed_mode,
                "observed": True,
                "reason": "read from the supplied response",
            }

        if self._scheduler is None or self._account is None:
            return {
                "measured": False,
                "current": None,
                "observed": False,
                "reason": "no scheduler or account attached",
            }

        slow_permitted = (
            self._scheduler.slow_thinking_allowed
            and self._account.state != EXHAUSTED
            and self._account.can_afford(self._scheduler.slow_cost)
        )
        return {
            "measured": False,
            "current": None,
            "observed": False,
            "permitted": SLOW if slow_permitted else FAST,
            "slow_allowed_by_trust": self._scheduler.slow_thinking_allowed,
            "slow_affordable": self._account.can_afford(self._scheduler.slow_cost),
            "reason": (
                "no response supplied; reporting the beat the current charge "
                "permits, not an observed one"
            ),
        }

    def _regime(self, energy: Dict[str, Any]) -> Dict[str, Any]:
        if not energy["measured"]:
            return {
                "measured": False,
                "current": None,
                "reason": "no energy account attached",
            }
        penalty_state = (
            self._scheduler.penalty_state if self._scheduler is not None else None
        )
        return {
            "measured": True,
            "current": evaluate_regime(
                energy["ratio"],
                recent_error_rate=self._recent_error_rate,
                critical_errors=self._critical_errors,
                penalty_state=penalty_state,
            ),
            "penalty_state": penalty_state,
            "reason": "measured",
        }

    def _channel(self) -> Dict[str, Any]:
        """L4 moves keys, not payloads; it keeps no state worth reporting.

        Reported as a constant with ``measured: False`` so a reader is not
        misled into thinking a number here was sampled.
        """
        return {
            "measured": False,
            "keys_only": True,
            "reason": "L4 transfers keys rather than payloads; there is no gauge to read",
        }

    def _brake(self) -> Dict[str, Any]:
        inflammatory = self._inflamed()
        if inflammatory is None:
            return {
                "attached": False,
                "tripped": None,
                "state": None,
                "reason": "no immune state attached",
            }
        tripped, state = inflammatory
        return {
            "attached": True,
            "tripped": tripped,
            "state": state,
            "reason": "measured",
        }

    def _inflamed(self):
        """Read the duck-typed immune state.

        Returns ``(tripped, state_label)`` or ``None`` when nothing is attached.
        Both duck-typed shapes are accepted -- an ``is_inflamed()`` method, or a
        ``state`` attribute -- because the two systems are separate
        distributions and neither should have to import the other's types.
        """
        immune = self._immune
        if immune is None:
            return None
        probe = getattr(immune, "is_inflamed", None)
        if callable(probe):
            try:
                tripped = bool(probe())
            except Exception:  # pragma: no cover - a broken probe is not "healthy"
                return (None, "unreadable")
            state = getattr(immune, "state", None)
            return (tripped, state if isinstance(state, str) else None)
        state = getattr(immune, "state", None)
        if isinstance(state, str):
            return (state == "INFLAMED", state)
        return (None, "unreadable")

    def _circadian(self) -> Dict[str, Any]:
        """L6 consolidates at night; whether it has run is not tracked here.

        Reported as unmeasured rather than ``False``: "no consolidation was
        recorded" and "no consolidation happened" are different claims, and this
        object has the evidence for neither.
        """
        return {
            "measured": False,
            "consolidated": None,
            "reason": "consolidation history is not tracked by the gateway",
        }

    def _compartment(self) -> Dict[str, Any]:
        manager = self._compartments
        if manager is None:
            return {
                "measured": False,
                "active": None,
                "total": None,
                "names": [],
                "reason": "no compartment manager attached",
            }
        alive = manager.alive_names()
        return {
            "measured": True,
            "active": len(alive),
            "total": len(manager.names()),
            "names": list(alive),
            "reason": "measured",
        }

    def _penalty(self) -> Dict[str, Any]:
        if self._scheduler is None:
            return {
                "measured": False,
                "penalty_state": None,
                "reason": "no penalty ledger attached",
            }
        state = self._scheduler.penalty_state
        return {
            "measured": state is not None,
            # Named ``penalty_state``, not ``state``: L5's key is the immune
            # state, and one shared name for two vocabularies is how a CRITICAL
            # ledger got read as a calm brake.
            "penalty_state": state,
            "reason": "measured" if state is not None else "no penalty ledger attached",
        }

    def __repr__(self) -> str:
        return "MetabolicGateway(account={}, scheduler={}, compartments={}, immune={})".format(
            self._account is not None,
            self._scheduler is not None,
            self._compartments is not None,
            self._immune is not None,
        )
