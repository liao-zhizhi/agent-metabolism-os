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
    --  double_entry.py  a claim and its evidence, booked twice and reconciled
    --  lean_auditor.py  holes in a machine-written proof, judged without reading it
    --  boundary_auditor.py  claims of crossing between physical layers, judged
                        on falsifiability and arithmetic, never on truth
    --  interface_engine.py  gaps between layers, and the fractal law that each
                        crossing narrows the opening and widens the reach

Standard library only. The energy figures are policy, not measurement, and the
latencies are modelled rather than slept.
"""

from .boundary_auditor import (
    DEFAULT_INVENTIONS,
    DEFAULT_LAYERS,
    MECHANISMS,
    MIN_STRUCTURAL_PARAMETERS,
    Boundary,
    BoundaryAudit,
    BoundaryAuditor,
    BoundaryAxis,
    BoundaryError,
    Invention,
    Layer,
    WitnessFault,
)
from .channel import (
    AgentProxy,
    ChannelError,
    MissingSubstrate,
    SubstrateChannel,
    SubstrateEntry,
    SubstrateKeyError,
)
from .agent_sdk import (
    AgentWarrantError,
    ArtefactMismatchError,
    MembraneAgent,
    MissingPromiseError,
    ToolOutcome,
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
from .double_entry import (
    # The canonical verdict values, exported once. `lean_auditor` repeats the
    # same three strings under LEAN_* names because that module also carries a
    # severity table; both spellings must stay identical to the lattice.
    FAIL,
    INVALID,
    PASS,
    AgentLocked,
    DoubleEntryError,
    DoubleEntryLedger,
    Receipt,
    Transaction,
    digest_file,
    verdict_for,
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
from .gateway import LAWS as METABOLIC_LAWS, MetabolicGateway
from .llm_proxy import (
    LLM_BEAT_FAST,
    LLM_BEAT_SLOW,
    LLM_RECORD_TYPE,
    LLMProxy,
    LedgerLockedError,
    ProxyResult,
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
from .interface_engine import (
    DEFAULT_AMPLIFICATION,
    NARROW_THRESHOLD,
    SPAN_HORIZON,
    FractalEvolver,
    Interface,
    InterfaceError,
    Layer as InterfaceLayer,
    format_span,
)
from .lean_auditor import (
    COMPILER,
    CUSTOM_AXIOM,
    HIDDEN_BODY,
    LEAN_FAIL,
    LEAN_INVALID,
    LEAN_PASS,
    LEAN_SEVERITY,
    LEAN_VERDICTS,
    PLACEHOLDER,
    STANDARD_AXIOMS,
    CompileReport,
    LeanAudit,
    LeanAuditorError,
    LeanFinding,
    LeanProofAuditor,
    LeanToolchain,
    UnavailableCompiler,
    mask_source,
)
from .scheduler import (
    ADAPTIVE_SURCHARGE,
    DEGRADED_ENERGY,
    DEGRADED_TRUST,
    FAST,
    OXPHOS,
    SLOW,
    SLP,
    TIER_ADAPTIVE,
    TIER_INNATE,
    TIERS,
    DualBeatScheduler,
    LLMResponse,
)

__version__ = "0.5.7"

__all__ = [
    "DEFAULT_INVENTIONS",
    "DEFAULT_LAYERS",
    "MECHANISMS",
    "MIN_STRUCTURAL_PARAMETERS",
    "DEFAULT_AMPLIFICATION",
    "FractalEvolver",
    "Interface",
    "InterfaceError",
    "InterfaceLayer",
    "NARROW_THRESHOLD",
    "SPAN_HORIZON",
    "format_span",
    "Boundary",
    "BoundaryAudit",
    "BoundaryAuditor",
    "BoundaryAxis",
    "BoundaryError",
    "Invention",
    "Layer",
    "WitnessFault",
    "ADAPTIVE_SURCHARGE",
    "FAIL",
    "INVALID",
    "PASS",
    "AgentLocked",
    "ALIVE",
    "COMPILER",
    "CUSTOM_AXIOM",
    "HIDDEN_BODY",
    "AgentWarrantError",
    "ArtefactMismatchError",
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
    "PLACEHOLDER",
    "PENALTY_BASE",
    "PENALTY_CRITICAL",
    "PENALTY_HEALTHY",
    "PENALTY_WARNING",
    "SLOW",
    "STANDARD_AXIOMS",
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
    "CompileReport",
    "DoubleEntryError",
    "DoubleEntryLedger",
    "DualBeatScheduler",
    "EnergyAccount",
    "EnergyError",
    "EnergyExhausted",
    "InsufficientEnergy",
    "KnowledgeFragment",
    "LLM_BEAT_FAST",
    "LLM_BEAT_SLOW",
    "LEAN_FAIL",
    "LEAN_INVALID",
    "LEAN_PASS",
    "LEAN_SEVERITY",
    "LEAN_VERDICTS",
    "LLM_RECORD_TYPE",
    "LLMProxy",
    "LLMResponse",
    "LedgerEntry",
    "LedgerLockedError",
    "LeanAudit",
    "LeanAuditorError",
    "LeanFinding",
    "LeanProofAuditor",
    "LeanToolchain",
    "METABOLIC_LAWS",
    "MembraneAgent",
    "MemoryFileError",
    "Message",
    "MetabolicGateway",
    "MissingPromiseError",
    "MissingSubstrate",
    "PenaltyError",
    "PenaltyLedger",
    "ProxyResult",
    "Receipt",
    "RegimeError",
    "RegimePlan",
    "RegimeSwitch",
    "SubstrateChannel",
    "SubstrateEntry",
    "SubstrateKeyError",
    "TIER_ADAPTIVE",
    "TIER_INNATE",
    "TIERS",
    "ToolOutcome",
    "Transaction",
    "UnavailableCompiler",
    "UnknownCompartment",
    "UnknownViolationType",
    "Violation",
    "__version__",
    "digest_file",
    "evaluate_regime",
    "mask_source",
    "verdict_for",
    "state_for",
]
