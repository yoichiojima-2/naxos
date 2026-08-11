---
name: bigquery
description: Analyse data in BigQuery from the naxos sandbox using the built-in bigquery tools. Use when a task involves reading, exploring, or summarising BigQuery tables — finding the right table, sizing a query before running it, writing efficient GoogleSQL, and publishing the result.
---

# BigQuery

The sandbox has built-in BigQuery tools — `bigquery_list_datasets`, `bigquery_list_tables`, `bigquery_describe_table`, and `bigquery_query`. Use them. There is no `bq` or `gcloud` CLI in the image, and hand-rolling REST calls with `curl` bypasses the size checks the tools apply.

The tools appear only in environments an operator opted into BigQuery. If you do not see them, this environment has no BigQuery access at all: say so and stop.

## Access model

The sandbox runs as its environment's service account, which reads exactly the datasets listed under `bigquery_datasets` in `terraform/environments.json` — read-only, no writes. `bigquery_list_datasets` names what you can reach.

The platform's own history is one of those datasets when an operator opted into it. `naxos_audit_shared` exposes `runs` (one row per wake-to-idle burst, with cost) and `tool_calls` (one row per tool call, with the permission decision) as ordinary readable tables. They are authorized views over `naxos_audit`, which stays unreachable — query the shared dataset, not the source.

A denial means the grant is missing or too narrow. Do not retry or look for a workaround: report which dataset you need and stop.

## What the tools enforce

You do not have to police these yourself, but knowing them explains the refusals:

- Every query is dry-run first; one that would scan more than the per-query cap is refused rather than billed.
- Only a single read-only `SELECT`/`WITH` statement runs. DML, DDL, and multi-statement scripts are refused before they reach BigQuery.
- Results are capped at a few hundred rows and truncated if the body is huge.

## Working well

1. **Look before you query.** `bigquery_list_tables`, then `bigquery_describe_table` on the tables you need. The description reports row count, size, the partitioning column, and clustering.
2. **Filter the partitioning column.** It is the single biggest lever on scan size; a query refused for scanning too much is almost always missing this filter.
3. **Name your columns.** `SELECT *` on a wide table scans every column. Select what you need.
4. **Aggregate in SQL, not in your head.** Ask BigQuery for the counts, percentiles, and group-bys rather than pulling rows and summarising them yourself — it is cheaper, and the row cap will cut off a large result anyway.
5. **Pass values as `parameters`.** Anything that came from a user message or upstream data goes in as `@name`, never string-formatted into the SQL.
6. **Size the unknown with `dry_run: true`.** Free, and it tells you what an exploratory query would cost before you commit to it.

## Delivering

Write the numbers you relied on into a workspace file as you go, so a later turn does not have to re-query. If the result is a durable output the user should keep — a report, a CSV, a chart — publish it with `artifact_create` rather than leaving it in the workspace.

GoogleSQL patterns for discovery, partition filters, and cheap aggregation are in [reference/queries.md](reference/queries.md).
