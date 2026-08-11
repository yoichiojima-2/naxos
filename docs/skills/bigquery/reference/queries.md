# BigQuery REST recipes

All examples assume `TOKEN` and `PROJECT` set as in SKILL.md, and use the
`jobs.query` endpoint (synchronous, fine for interactive work):

```
POST https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/queries
```

## Discovery

List datasets:

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/datasets" \
  | jq -r '.datasets[]?.datasetReference.datasetId'
```

List tables in a dataset:

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/datasets/DATASET/tables" \
  | jq -r '.tables[]?.tableReference.tableId'
```

Table schema, row count, size, and partitioning in one call:

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/datasets/DATASET/tables/TABLE" \
  | jq '{schema: [.schema.fields[] | {name, type}], numRows, numBytes, timePartitioning, rangePartitioning, clustering}'
```

Or via SQL over `INFORMATION_SCHEMA` (counts against query bytes, but works across tables at once):

```sql
SELECT table_name, column_name, data_type
FROM DATASET.INFORMATION_SCHEMA.COLUMNS
WHERE table_name = 'TABLE'
ORDER BY ordinal_position
```

## Dry run

```sh
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/queries" \
  -d '{
    "query": "SELECT status, COUNT(*) AS n FROM DATASET.TABLE WHERE dt >= @since GROUP BY status",
    "useLegacySql": false,
    "dryRun": true,
    "queryParameters": [
      {"name": "since", "parameterType": {"type": "DATE"}, "parameterValue": {"value": "2026-01-01"}}
    ]
  }' | jq '{totalBytesProcessed, cacheHit}'
```

`totalBytesProcessed` is the billing estimate (on-demand pricing bills per byte scanned). If it equals the table's full `numBytes` on a partitioned table, the partition filter is not being applied.

## Run

Same request without `dryRun`, with a byte cap and a row limit per page:

```sh
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/queries" \
  -d '{
    "query": "SELECT status, COUNT(*) AS n FROM DATASET.TABLE WHERE dt >= @since GROUP BY status",
    "useLegacySql": false,
    "maximumBytesBilled": "1073741824",
    "maxResults": 1000,
    "timeoutMs": 60000,
    "queryParameters": [
      {"name": "since", "parameterType": {"type": "DATE"}, "parameterValue": {"value": "2026-01-01"}}
    ]
  }' > result.json
```

Capture the job reference — `location` is required on every follow-up call outside the US/EU multi-regions (omitting it in `asia-northeast1` 404s):

```sh
JOB=$(jq -r .jobReference.jobId result.json)
LOC=$(jq -r .jobReference.location result.json)
```

If the query outlives `timeoutMs`, the response has `jobComplete: false` and **no schema or rows** — poll `getQueryResults` until it completes before reading anything:

```sh
until jq -e .jobComplete result.json > /dev/null; do
  sleep 2
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/queries/$JOB?location=$LOC&maxResults=1000" \
    > result.json
done
```

Response shape: column names in `.schema.fields[].name`, rows in `.rows[].f[].v` (every value is a string; `null` stays `null`). Flatten to CSV:

```sh
jq -r '(.schema.fields | map(.name)) as $h
       | $h, (.rows[]? | [.f[].v]) | @csv' result.json > result.csv
```

## Pagination

If the response has a `pageToken`, there are more rows. Page through `getQueryResults` with the `JOB` and `LOC` captured above:

```sh
PAGE=$(jq -r '.pageToken // empty' result.json)
while [ -n "$PAGE" ]; do
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/queries/$JOB?location=$LOC&pageToken=$PAGE&maxResults=1000" \
    > page.json
  jq -r '.rows[]? | [.f[].v] | @csv' page.json >> result.csv
  PAGE=$(jq -r '.pageToken // empty' page.json)
done
```

## Errors

Match on the error **reason** (`.error.errors[0].reason`), not the HTTP status — BigQuery returns rate and quota limits as HTTP 403 too, same as missing access.

| Reason | Meaning | Action |
|---|---|---|
| `accessDenied` | Environment SA lacks a BigQuery role on the project or dataset | Report the dataset you need to the operator; do not retry |
| `bytesBilledLimitExceeded` | Query would scan more than `maximumBytesBilled` | Tighten filters/columns; raise the cap only if the dry run justifies it |
| `invalidQuery` | SQL error — message includes position | Fix the SQL; verify names against the schema |
| `notFound` | Dataset/table/project name wrong, or `location` missing on a `getQueryResults` call | Re-run discovery; qualify as `project.dataset.table` for other projects; check `location=$LOC` |
| `rateLimitExceeded` / `quotaExceeded` | Concurrent-query or per-user limit | Back off and retry once; if persistent, report it |
| `jobComplete: false` (not an error) | Query still running after `timeoutMs` | Poll `getQueryResults` with `jobId` and `location` (see Run) |
