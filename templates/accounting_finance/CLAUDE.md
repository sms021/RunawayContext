# Work-type template: Accounting / Finance Tooling

You write or maintain software that touches general ledgers, AR, AP,
payroll, payapps, or project-accounting systems. The domain is
*literal* — totals must reconcile to the penny, and the rules behind
those totals are encoded across SQL, spreadsheets, vendor docs, and
unwritten conventions. This template is shaped by the patterns observed
in Parkway-style integration work (Vista / Procore / Monday / Paycom /
FileMaker).

## When to use this template

- Daily work integrates with at least one ERP or accounting system.
- "The number is wrong" is a routine bug report — and isn't always wrong.
- You depend on the difference between "is this row a cost or an
  allocation" being captured durably, not re-derived every quarter.

## Suggested initial slugs

- `general` — catch-all.
- `accounting` — ledger semantics, period close, sign conventions.
- `it` — infrastructure, scheduling, plumbing for the above.
- `vista` (or your ERP) — vendor-specific quirks.
- `procore` / `monday` / `paycom` — each integrated system.
- One slug per major tool you own (`ar_report`, `allocation_analysis`,
  `payapp_admin`).

## Suggested first lessons-learned categories

- **sign-conventions** — which side is debit, which side is credit, when
  did it flip last (e.g. "Safety allocation switched from JC CostAdj to
  GL Jrnl in March 2026").
- **trans-type-semantics** — when `trans_type` doesn't tell you what you
  think it does (use amount sign as the fallback).
- **period-close** — what counts as "in the period."
- **upsert-vs-soft-delete** — vendors that delete rows you have already
  synced; soft-delete handlers.
- **admin-gates** — auth boundaries that became gnarly (`hardcoded
  email allow-list in 3 files`).
- **rec-totals** — reconciliations that broke because two reports used
  different filter logic.

## RETRIEVAL.md paste-once block (tailored)

```
# RETRIEVAL — accounting / finance tooling

Before changing any allocation, cost-classification, or sign-handling
logic, run:

    runaway brief accounting
    runaway brief <slug-of-this-tool>

When a number reconciles wrong, search prior incidents first — the same
mistake happens twice on different reports:

    runaway search "<account> <symptom>" --project accounting

When you find a new sign rule, classification rule, or amount-driven
heuristic, log it with high reversibility (these errors are hard to
unwind once they hit a closed period):

    runaway log-lesson \
        --title "<short hook>" \
        --projects accounting,<tool-slug> \
        --what "<symptom>" \
        --why "<actual semantics>" \
        --fix "<the durable rule>" \
        --blast 5 --freq 2 --rev 5

When two reports drift because their filter logic diverges, propose a
shared rule:

    runaway propose-knowledge \
        --project accounting \
        --topic filter-parity \
        --title "<rule>" \
        --body "<the matching filter logic; affected files>"

Honor HR-2: register slugs before writing.

    runaway slug register <slug>
```

## Anti-patterns this template prevents

- Re-discovering that "balance-sheet escrows classify Cost vs
  Allocation by amount sign, NOT trans_type." Log it once; the brief
  surfaces it forever.
- Two reports' filter logic drifting because nobody noticed. The drift
  detector + cross-tool rules catch this.
- Vendor-specific quirks living in tribal memory. Lessons survive
  staff changes; tribal memory does not.
