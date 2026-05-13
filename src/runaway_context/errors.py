"""Typed exceptions raised by RunawayContext.

Every public method's docstring lists exactly which of these it raises
(HR-14: contracts are documented and machine-readable).

Refuses:
    Used by the system to signal a deliberate refusal — not a bug.
"""


class RunawayContextError(Exception):
    """Base for all RunawayContext refusals. Catch this if you must catch broadly."""


class InvalidProjectSlug(RunawayContextError):
    """A write was attempted with a slug that is not in the registry.

    Refuses:
        Writes without a valid canonical project slug (HR-2).
    """


class BriefBudgetExceeded(RunawayContextError):
    """The regenerator refused to write a brief that would exceed its tier cap.

    Refuses:
        Briefs over 150 lines (T3 cap), Constitution over 200, Living Memory over 50 (HR-5).
    """


class HardDeleteRefused(RunawayContextError):
    """A hard-delete was attempted on a path that does not allow it (HR-3)."""


class MaturationApprovalRequired(RunawayContextError):
    """The maturation engine attempted to mutate `maturity` directly (HR-9).

    The engine writes to `suggested_maturity` only. Use Client.mature_lesson()
    to apply a transition under human approval.
    """


class NetworkEgressBlocked(RunawayContextError):
    """A non-allowlisted module attempted to import or use a network library (HR-1)."""


class AuditChainBroken(RunawayContextError):
    """`runaway audit verify` found a break in the audit_log hash chain (HR-7)."""


class MigrationAborted(RunawayContextError):
    """The migrator detected a destructive change and aborted (HR-4).

    The backup is restored; nothing was applied.
    """


class TierGateFailed(RunawayContextError):
    """A `runaway tier promote` check did not pass (P5)."""


class ConflictReported(RunawayContextError):
    """JSON import found a row-level conflict that needs human resolution (T3)."""


class ProjectNotFound(RunawayContextError):
    """A read targeting a specific project found no project_context_card row.

    Refuses:
        Reads against projects that have not been registered or have no manifest.
    """


class MaturityEnumViolation(RunawayContextError):
    """Client detected ``lessons_learned.maturity`` values not in the canonical enum (HR-9).

    Refuses:
        Operating on a DB whose maturity values were mutated by external SQL.
    """
