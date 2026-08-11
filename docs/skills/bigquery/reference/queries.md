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
  | jq -r '.datasets[].datasetReference.datasetId'
```

List tables in a dataset:

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/datasets/DATASET/tables" \
  | jq -r '.tables[].tableReference.tableId'
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

Response shape: column names in `.schema.fields[].name`, rows in `.rows[].f[].v` (every value is a string; `null` stays `null`). Flatten to CSV:

```sh
jq -r '(.schema.fields | map(.name)) as $h
       | $h, (.rows[]? | [.f[].v]) | @csv' result.json > result.csv
```

## Pagination

If the response has a `pageToken`, there are more rows. Keep `jobReference.jobId` from the first response and page through `getQueryResults`:

```sh
JOB=$(jq -r .jobReference.jobId result.json)
PAGE=$(jq -r '.pageToken // empty' result.json)
while [ -n "$PAGE" ]; do
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://bigquery.googleapis.com/bigquery/v2/projects/$PROJECT/queries/$JOB?pageToken=$PAGE&maxResults=1000" \
    > page.json
  jq -r '.rows[]? | [.f[].v] | @csv' page.json >> result.csv
  PAGE=$(jq -r '.pageToken // empty' page.json)
done
```

Also check `jobComplete`: if `false`, the query is still running — poll the same `getQueryResults` URL (no `pageToken`) until it flips to `true` before reading rows.

## Errors

| Symptom | Meaning | Action |
|---|---|---|
| 403 `accessDenied` | Environment SA lacks a BigQuery role on the project or dataset | Report the dataset you need to the operator; do not retry |
| 400 `bytesBilledLimitExceeded` | Query would scan more than `maximumBytesBilled` | Tighten filters/columns; raise the cap only if the dry run justifies it |
| 400 `invalidQuery` | SQL error — message includes position | Fix the SQL; verify names against the schema |
| 404 `notFound` | Dataset/table name or its project is wrong | Re-run discovery; qualify as `project.dataset.table` if the data lives in another project |
| 429 / `rateLimitExceeded` | Concurrent query or per-user limit | Back off and retry once; if persistent, report it |
| `jobComplete: false` | Query still running after `timeoutMs` | Poll `getQueryResults` with the returned `jobId` |
