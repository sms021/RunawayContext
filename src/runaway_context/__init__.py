"""RunawayContext — a contract-enforced living context platform for AI coding assistants.

See docs/HARD_RULES.md for the 15 hard rules every implementation must honor.
"""

__version__ = "3.0.0"

from runaway_context.errors import (  # noqa: F401
    AuditChainBroken,
    BriefBudgetExceeded,
    ConflictReported,
    HardDeleteRefused,
    InvalidProjectSlug,
    MaturationApprovalRequired,
    MaturityEnumViolation,
    MigrationAborted,
    NetworkEgressBlocked,
    ProjectNotFound,
    RunawayContextError,
    TierGateFailed,
)

# Client is wired up after the package surface settles.
try:  # pragma: no cover - import order helper
    from runaway_context.client import Client  # noqa: F401
except ImportError:  # pragma: no cover
    Client = None  # type: ignore

__all__ = [
    "Client",
    "RunawayContextError",
    "InvalidProjectSlug",
    "BriefBudgetExceeded",
    "HardDeleteRefused",
    "MaturationApprovalRequired",
    "MaturityEnumViolation",
    "NetworkEgressBlocked",
    "AuditChainBroken",
    "MigrationAborted",
    "TierGateFailed",
    "ConflictReported",
    "ProjectNotFound",
    "__version__",
]
