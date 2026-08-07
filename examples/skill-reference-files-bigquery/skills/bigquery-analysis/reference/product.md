# Product reference

## Contents
1. API usage metrics
2. Feature adoption
3. DAU/MAU

## 1. API usage metrics

API usage is counted per authenticated request that returns a 2xx or 4xx
status (client errors count as usage; 5xx server errors do not, since they
represent a platform failure, not a customer action). Rate-limited (429)
requests do not count as usage.

## 2. Feature adoption

A feature is counted "adopted" by an account the first time any user on that
account completes the feature's primary action at least twice within a
rolling 30-day window — a single one-off use does not count as adoption,
to filter out accidental clicks.

## 3. DAU/MAU

DAU (daily active users) counts distinct authenticated users with at least
one non-read action in a calendar day. MAU (monthly active users) is the
distinct union of DAU over the trailing 30 days, not a sum of daily counts.
`DAU/MAU` (stickiness ratio) is reported as a rolling 7-day average to
smooth out weekday/weekend variance.
