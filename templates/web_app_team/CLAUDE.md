# Work-type template: Small Web-App Team (2–5 collaborators)

You are part of a small team building and operating one or more web apps.
The team is small enough that everyone touches both frontend and backend on
some days, large enough that knowledge sharing is no longer one person
remembering everything.

## When to use this template

- 2–5 active contributors.
- One or two related products (web app + admin tool, web app + landing
  page, etc.).
- You aspire to T3 — author attribution, JSON export/import of lessons,
  shared knowledge repo. The wizard leaves you at T1 or T2; you promote
  yourself when the gate passes.

## Suggested initial slugs

- `general` — catch-all.
- `frontend` — UI / routing / state.
- `backend` — API / persistence.
- `infra` — deploy, CI/CD, secrets, observability.
- `design_system` — shared UI primitives, tokens, components.
- One slug per top-level product (`web_app`, `admin_tool`, `landing`).

## Suggested first lessons-learned categories

- **deploy-incidents** — what broke on deploy and how you fixed it.
- **auth** — sessions, tokens, SSO bindings, weird edge cases.
- **database-migrations** — schema changes that were not as trivial as
  they looked.
- **third-party-integrations** — quirks of the SaaS APIs you depend on.
- **performance** — N+1s, fan-out queries, frontend payload bloat.
- **onboarding** — things every new contributor trips over.

## RETRIEVAL.md paste-once block (tailored)

Drop this in your monorepo root as `RETRIEVAL.md`. Each contributor's AI
assistant will use it at session start. Lessons are tagged with project
slugs; the team's shared knowledge repo (T3 unlock) syncs them out via
`runaway export` / `runaway import`.

```
# RETRIEVAL — small web-app team

When the session starts in a subdirectory matching a known slug, the AI
should run:

    runaway brief <slug>

For incidents and onboarding gotchas:

    runaway list-lessons --project <slug> --status active

When the team agrees on a new rule (review comment, postmortem outcome),
propose it via the drafts inbox so it can be approved before it lands:

    runaway propose-knowledge \
        --project <slug> \
        --topic <topic> \
        --title "<short hook>" \
        --body "<rationale + concrete how>" \
        --tags <comma,tags>

Drafts are reviewed by a teammate or the on-call AI:

    runaway list-drafts
    runaway approve-draft <draft_id> --actor <name>
    runaway reject-draft <draft_id> --actor <name> --notes "<why>"

Honor HR-2: register slugs before writing.

    runaway slug register <slug> --description "<short>"
```

## Tier-progression hint

Aim for T3. The gate is: a second `author_id` has logged at least one
approved lesson in the last 30 days. The wizard's MCP and telemetry flags
are at your discretion — they unlock at T2.

## Anti-patterns this template prevents

- Postmortems that live only in a Notion page. Capture the rule via
  `propose-knowledge`; let the next on-call's AI surface it.
- Onboarding docs that go stale. Lessons mature (HR-9): they age out of
  the always-loaded brief into `internalized` once everyone has
  internalized them.
- "We talked about this last quarter." Search first:
  `runaway search "<keyword>"`.
