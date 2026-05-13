# Work-type template: Data Engineer (pipelines, ETL, warehouses)

You build and operate data pipelines. Sources are noisy. Schemas drift.
Yesterday's CSV had three columns; today's has five and one is renamed.
RunawayContext captures the "and the reason we do X is …" knowledge that
otherwise lives only in your head or a half-decayed wiki page.

## When to use this template

- Daily work involves SQL, Python (pandas / polars / pyarrow / dbt /
  Airflow / Dagster / Prefect), and one or more warehouses.
- Sources include some combination of CSV drops, vendor APIs, internal
  application DBs, and partner SFTP.
- You are accountable for both correctness ("this row's count is right")
  and lineage ("here's why we ignored that source for two months").

## Suggested initial slugs

- `general` — catch-all.
- `lineage` — cross-system mappings; cross-references the
  `claude_database_map.md` discipline.
- `dbt` — the dbt project itself.
- `airflow` — DAGs, schedulers, sensors.
- `quality` — assertions, expectations, dq checks.
- One slug per warehouse / lakehouse (`snowflake`, `bigquery`,
  `databricks`).
- One slug per upstream source you do real work against
  (`shopify_sync`, `salesforce_sync`).

## Suggested first lessons-learned categories

- **schema-drift** — sources that quietly changed.
- **timezone** — when UTC was assumed and wasn't.
- **late-arriving** — joins that broke when the dim table updated later
  than the fact.
- **deduplication** — what counts as "the same row."
- **incremental-pitfalls** — watermark / cursor bugs.
- **cost** — queries that were correct but expensive.
- **vendor-quirks** — undocumented API behaviors you discovered the hard
  way.

## RETRIEVAL.md paste-once block (tailored)

```
# RETRIEVAL — data engineer

Before writing or modifying a pipeline, run:

    runaway brief <slug-of-this-pipeline-or-warehouse>

When debugging a row-count anomaly, search prior lessons first:

    runaway search "<source> <symptom>" --project <slug>

When a source's schema changes or you discover a vendor quirk, log it
with severity axes that reflect the actual impact:

    runaway log-lesson \
        --title "<short hook>" \
        --projects <slug>,lineage \
        --what "<what changed>" \
        --why "<root cause>" \
        --fix "<the durable fix>" \
        --blast 4 --freq 2 --rev 4

Cross-system data mappings (the T2.5 surface) live in the same store:

    runaway propose-knowledge \
        --project lineage \
        --topic mapping \
        --title "<source>.<table> -> <warehouse>.<table>" \
        --body "<mapping notes; column-level decisions; gotchas>" \
        --tags mapping,<source>,<warehouse>

Honor HR-2: register slugs before writing.

    runaway slug register <slug>
```

## Anti-patterns this template prevents

- "We've seen this before but I can't remember where." Search first.
- A new pipeline forgetting the watermark trick the old one learned.
  Lessons are tagged with multiple projects (`--projects a,b`).
- Lineage in a Confluence page that nobody updates. Lineage in the
  knowledge store is queryable, mature-able, and exportable.
