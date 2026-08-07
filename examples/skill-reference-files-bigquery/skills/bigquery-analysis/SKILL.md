---
name: bigquery-analysis
description: Answers questions about company revenue, sales pipeline, and product usage metrics by looking up definitions in domain-specific reference files. Use when the user asks about ARR/billing/revenue, sales pipeline/opportunities, or product/API usage adoption metrics.
---

# BigQuery analysis

This skill answers metric questions by first looking up the exact definition
in the relevant domain reference file, then reasoning from that definition.
Each domain has its own reference file. Only read the one(s) the current
question actually needs — the others can stay unread.

- Revenue, ARR, billing cycles, revenue recognition -> read
  [reference/finance.md](reference/finance.md)
- Sales pipeline, opportunity stages, quota attainment -> read
  [reference/sales.md](reference/sales.md)
- API usage, feature adoption, DAU/MAU -> read
  [reference/product.md](reference/product.md)

Read a reference file with the `read_reference` tool before answering a
question in its domain — do not guess metric definitions from general
knowledge. If a question spans more than one domain, read each relevant file
in turn.
