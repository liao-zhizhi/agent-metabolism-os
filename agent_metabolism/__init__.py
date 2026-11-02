"""agent-metabolism-os -- metabolic regulation wrapped around a stateless LLM.

The model is treated as a CPU with no memory of its own charge: ask for a long
chain of thought on the last joule and it will produce one. The regulation has
to live outside it, which is what this package is.

    L1  energy.py        a finite charge that must be paid before thinking
    L2  scheduler.py     two beats, chosen from complexity, affordability and trust
    L3  regime.py        how much of the organism runs: GROW / CONSERVE / EMERGENCY
    L4  channel.py       hand over a key, not a payload; the substrate is shared
    L6  circadian.py     night mode compresses the day's context into memory
    L7  compartment.py   membrane-bounded units; one can lyse without the rest
    --  penalty.py       accumulated damage: a fine and a trust score, read by L2/L3

Standard library only. The energy figures are policy, not measurement, and the
latencies are modelled rather than slept.
"""

from .channel import (
    AgentProxy,
    ChannelError,
    MissingSubstrate,
    SubstrateChannel,
    SubstrateEntry,
    SubstrateKeyError,
)
from .circadian import (
    CONSOLIDATION_METHOD,
    CircadianError,
    CircadianScheduler,
    ConsolidationReport,
    KnowledgeFragment,
    MemoryFileError,
)
from .compartment import (
    ALIVE,
    LYSED,
    Compartment,
    CompartmentDestroyed,
    CompartmentError,
    CompartmentManager,
    Message,
    UnknownCompartment,
)
from .energy import (
    CONSERVE,
    EXHAUSTED,
    GROW,
    EnergyAccount,
    EnergyError,
    EnergyExhausted,
    InsufficientEnergy,
    LedgerEntry,
)
from .penalty import (
    CRITICAL as PENALTY_CRITICAL,
    DEFAULT_WEIGHTS,
    HEALTHY as PENALTY_HEALTHY,
    PENALTY_BASE,
    PenaltyError,
    PenaltyLedger,
    UnknownViolationType,
    Violation,
    WARNING as PENALTY_WARNING,
    state_for,
)
from .regime import (
    CRITICAL_ERROR_LIMIT,
    EMERGENCY,
    MAX_CONCURRENT_AGENTS,
    RegimeError,
    RegimePlan,
    RegimeSwitch,
    evaluate_regime,
)
from .scheduler import (
    DEGRADED_ENERGY,
    DEGRADED_TRUST,
    FAST,
    OXPHOS,
    SLOW,
    SLP,
    DualBeatScheduler,
    LLMResponse,
)

__version__ = "0.3.0"

__all__ = [
    "ALIVE",
    "CONSERVE",
    "CONSOLIDATION_METHOD",
    "CRITICAL_ERROR_LIMIT",
    "DEFAULT_WEIGHTS",
    "DEGRADED_ENERGY",
    "DEGRADED_TRUST",
    "EMERGENCY",
    "EXHAUSTED",
    "FAST",
    "GROW",
    "LYSED",
    "MAX_CONCURRENT_AGENTS",
    "OXPHOS",
    "PENALTY_BASE",
    "PENALTY_CRITICAL",
    "PENALTY_HEALTHY",
    "PENALTY_WARNING",
    "SLOW",
    "SLP",
    "AgentProxy",
    "ChannelError",
    "CircadianError",
    "CircadianScheduler",
    "Compartment",
    "CompartmentDestroyed",
    "CompartmentError",
    "CompartmentManager",
    "ConsolidationReport",
    "DualBeatScheduler",
    "EnergyAccount",
    "EnergyError",
    "EnergyExhausted",
    "InsufficientEnergy",
    "KnowledgeFragment",
    "LLMResponse",
    "LedgerEntry",
    "MemoryFileError",
    "Message",
    "MissingSubstrate",
    "PenaltyError",
    "PenaltyLedger",
    "RegimeError",
    "RegimePlan",
    "RegimeSwitch",
    "SubstrateChannel",
    "SubstrateEntry",
    "SubstrateKeyError",
    "UnknownCompartment",
    "UnknownViolationType",
    "Violation",
    "__version__",
    "evaluate_regime",
    "state_for",
]
