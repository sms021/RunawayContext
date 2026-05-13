# Work-type template: Solo Python Developer

You are a solo Python developer working across a small set of personal or
contract projects. RunawayContext gives you a shared memory across sessions
and machines without leaking context between unrelated projects.

## When to use this template

- One person, one machine (or a personal laptop + workstation pair).
- 1..5 Python projects active at any time.
- You install your own dependencies, manage your own virtualenvs, and own
  your own CI.

## Suggested initial slugs

Register these the first time you run `runaway init`. Add more later with
`runaway slug register <slug>`.

- `general` — catch-all for lessons that aren't project-specific.
- `tooling` — your local toolchain (uv, pip, pyproject, pre-commit).
- `home_lab` — anything you run on your personal infrastructure.
- One slug per active project (e.g. `myproject`, `client_acme`).

## Suggested first lessons-learned categories

These are the buckets where solo Python devs accumulate scar tissue fastest.
Treat each as a tag/topic, not a fixed slug.

- **environment** — venv / poetry / uv / system-Python conflicts.
- **packaging** — `pyproject.toml`, entry points, wheel vs sdist.
- **testing** — pytest fixtures that bit you, async test gotchas.
- **typing** — mypy / pyright quirks, Protocol/ABC trade-offs.
- **async** — event-loop pitfalls, blocking I/O in coroutines.
- **deps** — version pins that broke transitive resolution.
- **deploy** — manual deploy steps you keep re-discovering.

## RETRIEVAL.md paste-once block (tailored)

Drop this verbatim into your project root as `RETRIEVAL.md`. Your AI assistant
will use it to fetch context at the start of every session.

```
# RETRIEVAL — solo python developer

Before answering any question that mentions code in this repo, run:

    runaway brief <slug-of-this-project>

If the brief mentions a lesson (LL#N), retrieve its body with:

    runaway list-lessons --project <slug> --status active

When you spot a behavior worth remembering, log it as a lesson immediately:

    runaway log-lesson \
        --title "<short hook>" \
        --projects <slug> \
        --what "<symptom>" \
        --why "<root cause>" \
        --fix "<the durable fix>" \
        --blast 2 --freq 3 --rev 2

Search before re-deriving:

    runaway search "<keyword>" --project <slug>

Honor HR-2: tag every write with a registered project slug. If a slug does
not exist yet, register it before writing:

    runaway slug register <slug>
```

## Promotion-gate hint

You start at T1. To unlock T2 (MCP, telemetry, semantic retrieval), you need
30 days of use, 10 lessons across 2 projects, and at least one drift warning
logged. The wizard puts you on the path; you do not need to plan for it.

## Anti-patterns this template prevents

- Logging lessons against `general` because you can't be bothered to register
  a project slug. (Use `runaway slug register` once; reuse forever.)
- Re-deriving the same fix twice. If you Googled it, log it.
- Treating `notes.md` as memory. The brief is your memory; notes are
  scratch.
