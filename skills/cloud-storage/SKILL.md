---
name: cloud-storage
description: How to investigate files in Google Cloud Storage with the list_gcs_objects, get_gcs_object_info, and read_gcs_object tools. Consult this whenever a task involves reading files, log archives, exports, backups, or any bucket contents — including indirect asks like "check the export", "what's in the bucket", or "read the latest dump".
---

# Investigating with Cloud Storage

## Workflow: locate, inspect, then read

Reading blind wastes turns on wrong paths and truncated or binary
content. Work in three steps:

1. `list_gcs_objects` to find the object. Listings are capped, so use
   `prefix` to narrow down — object paths are usually structured like
   directories (`logs/2026-07-28/`, `exports/daily/`), and date-shaped
   prefixes are the fastest way to the most recent file.
2. `get_gcs_object_info` on the object before reading. Two fields decide
   your next move:
   - **content_type** — only read text formats (`text/*`,
     `application/json`, CSV). Binary content (gzip, parquet, images)
     comes back garbled; report its existence and metadata instead of
     reading it.
   - **size_bytes** — reads are capped at ~1 MB. Anything larger comes
     back truncated (see below).
3. `read_gcs_object` to get the content.

## Working with the read cap

Reads beyond the cap end with an explicit marker:
`...[truncated: showing X of Y bytes]`.

- **Treat truncated content as a sample, not the whole file.** You saw
  the beginning only — never claim a file "does not contain" something
  based on a truncated read, and say "the first ~1 MB shows..." when
  reporting from one.
- Large files are often date- or hour-split into many smaller objects —
  prefer listing for a narrower shard over reasoning from a truncated
  read of a big one.

## When a tool call fails

Errors come back as the tool result. `not found` usually means a wrong
path, not a missing file: list the parent prefix and check the exact
spelling before concluding anything. If a bucket itself errors, you may
not have access to it — report that as a finding rather than retrying.

## Report with evidence

Cite the object you read: full `gs://bucket/path`, its `updated`
timestamp, and whether the read was truncated. Freshness matters — an
"error in the logs" from an object last updated three weeks ago is a
different finding than one from ten minutes ago. If you could not read
the relevant object (binary, too large, access denied), state that
explicitly rather than concluding without it.
