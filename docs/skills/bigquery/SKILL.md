---
name: bigquery
description: Query BigQuery datasets from the naxos sandbox. Use when a task involves reading, analysing, or exporting data in BigQuery tables — discovering datasets and schemas, estimating query cost, running SQL, and publishing results. Covers the REST API with metadata-server auth (no bq/gcloud CLI in the sandbox).
---

# BigQuery

Run queries against BigQuery from inside the sandbox. The sandbox image has **no `bq` or `gcloud` CLI** — use the BigQuery REST API with `curl`, authenticated by the sandbox's own service account via the metadata server.

## Access model

The sandbox runs as its environment's service account. By default that account has **no BigQuery access** — an operator must opt the environment in by listing datasets under `bigquery_datasets` in `terraform/environments.json` and applying, which grants `roles/bigquery.jobUser` plus `roles/bigquery.dataViewer` on exactly those datasets.

A `403` with `accessDenied` means the grant is missing or too narrow. Do not retry or look for workarounds: report which dataset you need and stop.

## Auth

Fetch an access token from the metadata server and pass it as a bearer token:

```sh
TOKEN=$(curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
  | jq -r .access_token)
PROJECT=$(curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/project/project-id")
```

Tokens expire after ~1 hour; re-fetch rather than caching across long tasks.

## Rules

1. **Dry-run every query first.** Set `"dryRun": true`, read `totalBytesProcessed`, and reconsider anything over ~10 GB before running it for real.
2. **Always set `maximumBytesBilled`** on the real run (default to `1073741824` — 1 GB — unless the dry run justifies more). A query over the cap fails instead of billing.
3. **Never `SELECT *`** on tables you have not inspected. Look at the schema first and select the columns you need.
4. **Filter partitioned tables on their partition column.** The dry run exposes the miss: full-table bytes scanned on a table with a `_PARTITIONTIME` or date partition means your filter is wrong.
5. **Read-only by default.** No DML/DDL (`INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`) unless the task explicitly asks for it — and expect it to fail unless the operator granted write roles.
6. **Use query parameters** for any value that comes from user input or upstream data — never interpolate strings into SQL.

## Workflow

1. **Discover** — list datasets and tables, then read the schema of the tables you need (recipes in [reference/queries.md](reference/queries.md)).
2. **Dry-run** the SQL; check `totalBytesProcessed`.
3. **Run** with `maximumBytesBilled` set; page through results with `pageToken` if `totalRows` exceeds the first page.
4. **Deliver** — write results to a workspace file (CSV/JSON). If the result is a durable output the user should keep, publish it with the `artifact_create` tool rather than leaving it in the workspace.

All request shapes, discovery queries (`INFORMATION_SCHEMA`), pagination, parameterized queries, and error handling are in [reference/queries.md](reference/queries.md).
