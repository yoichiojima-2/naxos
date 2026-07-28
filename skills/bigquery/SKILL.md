---
name: bigquery
description: How to investigate data in BigQuery with the query_bigquery, get_table_info, and list_tables tools. Consult this whenever a task involves reading logs, metrics, audit records, or any tabular data — including indirect asks like "check for anomalies", "how many errors occurred", or "when did this start".
---

# Investigating with BigQuery

## Workflow: schema first, then query

Never guess table or column names. The cost of checking is one cheap tool
call; the cost of guessing is a failed query and a wasted turn.

1. `list_tables` on the dataset if you don't know what exists.
2. `get_table_info` on the table you plan to query. Read three things:
   - **schema** — exact column names, types, and descriptions
   - **partitioning** — if set, always filter on that column (see below)
   - **size_mb** — large table means aggregate, don't select raw rows
3. Write the query. Results arrive as JSON rows.

## Writing queries that fit the guardrails

Every query runs under a scan-size cap and a result cap of ~200 rows.
These are platform limits, not suggestions — work with them:

- **Aggregate instead of dumping rows.** You investigate distributions,
  counts, and time buckets; you rarely need raw rows. `GROUP BY` +
  `COUNT`/`AVG` answers most questions within the row cap. If you do need
  raw rows (e.g. sample error messages), take a small `LIMIT` of them.
- **Filter on the partition column first.** On partitioned tables, a
  `WHERE` on the partition column is what cuts scan size. Most
  investigations only need a recent window: start with the last hour or
  day, widen only if needed.
- **Select only the columns you need.** BigQuery bills by columns
  scanned; `SELECT *` on a wide table is the main way to hit the cap.
- If a query is rejected with `bytesBilledLimitExceeded`, it scanned too
  much: narrow the time window, drop columns, or aggregate further. Do
  not retry the same query unchanged.

## When a query fails

The error message comes back as the tool result, and BigQuery's messages
are precise (`Unrecognized name: colunm at [3:8]`). Read the message, fix
that exact issue, retry once. If the same query fails twice for different
reasons, go back to `get_table_info` — your mental model of the table is
probably wrong.

## Report with evidence

A finding without numbers is not a finding. Every conclusion you report
must cite the query result that supports it: the metric, the value, and
the time window it came from ("5xx rate rose from 0.2% to 4.1% between
09:00–09:15 UTC, n=12,400 requests"). If you could not obtain supporting
data, say so explicitly rather than concluding without it. Reports
lacking evidence are blocked before posting.
