# Work-type template: Documentation Writer

You write docs. Maybe you write *only* docs — technical writer, content
designer, API documentarian. Maybe you are an engineer who is the team's
de facto docs maintainer. Either way, your output is read by people
making decisions, and the cost of stale or conflicting docs compounds.
RunawayContext gives you a memory of *why* the docs say what they say.

## When to use this template

- Primary deliverable is markdown, reST, or rendered docs (mkdocs,
  Sphinx, Docusaurus, ReadTheDocs, etc.).
- You frequently need to reconcile docs across versions, products, or
  audiences.
- "We agreed to phrase it that way" is something you need to be able to
  recall six months later.

## Suggested initial slugs

- `general` — catch-all.
- `style_guide` — voice / tense / capitalization / link style.
- `terminology` — disambiguated terms, abbreviations, brand names.
- One slug per product whose docs you own (`web_app_docs`,
  `cli_docs`, `api_docs`).
- `release_notes` — long-lived rules about what counts as a release.

## Suggested first lessons-learned categories

- **terminology-decisions** — why we picked term X over term Y.
- **deprecation-policy** — when something is "deprecated" vs "removed"
  vs "discouraged."
- **versioning** — what counts as breaking, what counts as minor.
- **audience** — when a doc is for end-users, when it's for ops.
- **assets** — image dimensions, alt-text rules, CDN paths.
- **localization** — what gets translated, what doesn't.

## RETRIEVAL.md paste-once block (tailored)

```
# RETRIEVAL — documentation writer

Before editing a doc, check the relevant brief:

    runaway brief style_guide
    runaway brief terminology
    runaway brief <slug-of-this-doc-set>

When you're about to write "I think we call this X," search:

    runaway --term "<abbreviation>"      # if available in your install
    runaway search "<term>" --project terminology

Capture the decision when you make it (not later):

    runaway log-lesson \
        --title "<short rule>" \
        --projects style_guide \
        --what "<observed problem>" \
        --why "<reason for the rule>" \
        --fix "<the rule>" \
        --blast 3 --freq 5 --rev 1

For team-shared terminology, propose to the drafts inbox so a colleague
can sanity-check:

    runaway propose-knowledge \
        --project terminology \
        --topic vocab \
        --title "<term>" \
        --body "<definition; do-say; don't-say; rationale>"

Honor HR-2: register slugs before writing.

    runaway slug register <slug>
```

## Anti-patterns this template prevents

- Docs that say two different things about the same feature in two
  places because nobody remembered the original decision. The brief
  surfaces the rule each session.
- Style-guide PRs that bikeshed because the conversation never
  happened in writing. Capture each decision as a lesson — even if
  it's two sentences.
- Re-deciding deprecation phrasing on every release. Internalized
  lessons drop from the brief but stay queryable.
