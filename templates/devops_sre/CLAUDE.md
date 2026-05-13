# Work-type template: DevOps / SRE / Platform

You operate infrastructure. The cost of forgetting a lesson is measured
in minutes of downtime, not minutes of refactor. RunawayContext is the
durable substrate behind your runbooks.

## When to use this template

- You own clusters, VMs, container hosts, or serverless platforms.
- You are on-call (or write what on-call follows).
- Your changes touch IaC (Terraform, Pulumi, Crossplane), CI/CD
  pipelines, secrets stores, and observability stacks.

## Suggested initial slugs

- `general` — catch-all.
- `infra` — physical / virtual / cloud resources.
- `iac` — Terraform / Pulumi / Crossplane modules.
- `cicd` — GitHub Actions / GitLab CI / Buildkite / etc.
- `observability` — metrics, logs, traces, dashboards.
- `secrets` — secret stores, rotation runbooks.
- `network` — VPCs, firewalls, DNS.
- One slug per critical service you own (`gateway`, `auth_svc`,
  `payments_svc`).

## Suggested first lessons-learned categories

- **incidents** — postmortems condensed into a recallable rule.
- **deploys** — non-obvious deploy ordering, blast-radius surprises.
- **secret-rotation** — workflows you had to re-derive at 2am.
- **dns** — TTL traps, split-horizon weirdness.
- **iam** — least-privilege gotchas; "this role couldn't do X because
  of Y."
- **scaling** — autoscaler edge cases, cold-start traps.
- **storage** — disk-fill, EBS attach quirks, GCS / S3 oddities.

## RETRIEVAL.md paste-once block (tailored)

Drop in your runbook root or repo root as `RETRIEVAL.md`. On-call AIs
load it on every session.

```
# RETRIEVAL — devops / sre

When paged, run:

    runaway brief <slug-of-affected-service>
    runaway list-lessons --project <slug> --status active

When a runbook step works but seems undocumented, search:

    runaway search "<error or symptom>" --project <slug>

Postmortem outputs go to the store as soon as the page is resolved.
Use high blast and reversibility to make incidents surface in briefs:

    runaway log-lesson \
        --title "<short hook>" \
        --projects <slug>,infra \
        --what "<symptom>" \
        --why "<root cause>" \
        --fix "<the durable fix or runbook pointer>" \
        --blast 5 --freq 2 --rev 4

For team-wide platform rules (e.g. "rotate this secret monthly via
script X"), propose to the drafts inbox so it can be reviewed:

    runaway propose-knowledge \
        --project infra \
        --topic runbook \
        --title "<short hook>" \
        --body "<step-by-step + when to use>"

Honor HR-2: register slugs before writing.

    runaway slug register <slug>
```

## Anti-patterns this template prevents

- Runbooks that nobody updates because they are not where you look
  when paged. Lessons live in the brief, the brief lives wherever the
  AI assistant looks.
- "Tribal knowledge" that retires with the senior engineer. Lessons
  are tagged opaquely (HR-6) and survive personnel changes.
- Re-deriving the same iptables / Terraform / IAM trick six months
  later. Search; if it isn't there, log it now.
