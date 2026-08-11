# GoogleSQL patterns

SQL to pass to `bigquery_query`. Table names are `dataset.table` (the project is implied) or `` `project.dataset.table` `` when you need to be explicit.

## Discovery

`bigquery_list_tables` and `bigquery_describe_table` cover the common case. `INFORMATION_SCHEMA` answers the rest — it is metadata, so these queries are cheap.

Tables with their sizes, largest first:

```sql
SELECT table_id, row_count, ROUND(size_bytes / POW(1024, 3), 2) AS gib
FROM analytics.__TABLES__
ORDER BY size_bytes DESC
```

Find a column across a dataset:

```sql
SELECT table_name, column_name, data_type
FROM analytics.INFORMATION_SCHEMA.COLUMNS
WHERE LOWER(column_name) LIKE @pattern
ORDER BY table_name
```

with `parameters: {"pattern": "%user_id%"}`.

Which column a table is partitioned on:

```sql
SELECT table_name, column_name
FROM analytics.INFORMATION_SCHEMA.COLUMNS
WHERE is_partitioning_column = 'YES'
```

## Partition filters

The filter has to be on the partitioning column itself, compared to a constant or a parameter — wrapping it in a function defeats pruning.

```sql
-- prunes
SELECT COUNT(*) FROM analytics.events
WHERE event_date BETWEEN @start AND @end

-- does not prune: the column is inside a function
SELECT COUNT(*) FROM analytics.events
WHERE FORMAT_DATE('%Y-%m', event_date) = '2026-08'
```

For ingestion-time partitioning the pseudo-column is `_PARTITIONDATE` (or `_PARTITIONTIME`):

```sql
SELECT COUNT(*) FROM analytics.events
WHERE _PARTITIONDATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
```

Confirm the filter works with `dry_run: true` — if the estimate is the whole table, it is not pruning.

## Sampling and shape

Look at a table's shape without scanning it:

```sql
SELECT * FROM analytics.events
WHERE event_date = @day
LIMIT 20
```

`LIMIT` does **not** reduce bytes scanned; the partition filter does. On an unpartitioned table use `TABLESAMPLE SYSTEM (1 PERCENT)` instead.

## Aggregation

Prefer one aggregate query over pulling rows and counting them yourself:

```sql
SELECT
  event_name,
  COUNT(*) AS events,
  COUNT(DISTINCT user_id) AS users,
  APPROX_QUANTILES(duration_ms, 100)[OFFSET(50)] AS p50_ms
FROM analytics.events
WHERE event_date BETWEEN @start AND @end
GROUP BY event_name
ORDER BY events DESC
```

`APPROX_COUNT_DISTINCT` and `APPROX_QUANTILES` are much cheaper than their exact forms on large tables and are usually accurate enough for an analysis.

## Paging a large result

The row cap is on what comes back to you, not on what the query computes. Page in SQL when you genuinely need every row:

```sql
SELECT id, name FROM analytics.customers
ORDER BY id
LIMIT 200 OFFSET @offset
```

Better, when it fits the task: aggregate or filter until the answer is small enough to read in one go.

## Nested and repeated fields

`bigquery_describe_table` shows nested fields as `parent.child`. Unnest to filter or count them:

```sql
SELECT event_name, param.key, COUNT(*) AS n
FROM analytics.events, UNNEST(event_params) AS param
WHERE event_date = @day
GROUP BY event_name, param.key
```

Repeated values come back as JSON arrays, and records as nested objects.

## Errors worth recognising

| What you see | What it means |
|---|---|
| denied / not authorised | the dataset is not in this environment's grant — report it and stop |
| would scan more than the cap | add or fix the partition filter, select fewer columns, aggregate |
| `Not found: Table …` | wrong dataset or table id; re-check with `bigquery_list_tables` |
| `Unrecognized name` | wrong column; re-check with `bigquery_describe_table` |
| refused: only read-only SELECT | this environment has read access only, by design |
