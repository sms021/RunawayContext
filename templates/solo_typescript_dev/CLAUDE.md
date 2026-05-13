# Work-type template: Solo TypeScript / JavaScript Developer

You are a solo developer working across a small set of TS/JS projects —
Node services, scripts, browser frontends, or some combination. The
template biases toward the recurring gotchas of the TS/JS ecosystem
(module systems, build tooling, package manager churn).

## When to use this template

- One person owns the codebase.
- Stack is anchored in TypeScript / JavaScript (Node + browser welcome).
- You use one of npm / pnpm / yarn / bun and pick your own bundler.

## Suggested initial slugs

- `general` — catch-all.
- `tooling` — node / pnpm / corepack / asdf / Volta.
- One slug per active project (e.g. `mywebapp`, `apicli`).
- `notes` — drafts that haven't earned a project yet.

## Suggested first lessons-learned categories

- **module-system** — ESM vs CJS, `"type": "module"`, `.cjs/.mjs` rules.
- **typescript-config** — `moduleResolution`, `paths`, `composite`,
  references.
- **bundlers** — Vite/esbuild/Rollup/Webpack idiosyncrasies.
- **pnpm-workspaces** — hoisting, `workspace:*` versioning, lockfile drift.
- **node-version** — engines field, `nvm`/`volta` traps.
- **frontend-runtime** — hydration mismatches, suspense pitfalls.
- **deno-bun** — when alternate runtimes change semantics.

## RETRIEVAL.md paste-once block (tailored)

Drop this in your repo root as `RETRIEVAL.md`. Your AI assistant runs these
commands at session start.

```
# RETRIEVAL — solo typescript developer

Before reasoning about code in this repo, run:

    runaway brief <slug-of-this-project>

If the brief mentions LL#N, retrieve it with:

    runaway list-lessons --project <slug> --status active

When you finally figure out a TS or build-tool quirk, log it the same minute:

    runaway log-lesson \
        --title "<short hook>" \
        --projects <slug> \
        --what "<symptom>" \
        --why "<root cause>" \
        --fix "<the durable fix>" \
        --blast 2 --freq 4 --rev 1

Search before guessing:

    runaway search "esm cjs" --project <slug>

Honor HR-2: tag every write with a registered slug. If a slug does not
exist yet, register it before writing:

    runaway slug register <slug>
```

## Anti-patterns this template prevents

- Re-debugging the same ESM/CJS interop issue across projects. Log it once.
- Trying yet another package manager because the last one had a weird
  cache. Lessons about pnpm are recallable; folk advice on Discord is not.
- Letting `tsconfig.json` choices drift between repos. Log the rationale
  for each non-default setting.
