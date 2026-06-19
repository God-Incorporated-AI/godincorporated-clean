# God Incorporated — Phase 11.8D Reporting, Alerting, Notifications, Backup, and Fallback Plan

This runbook records the Phase 11.8D plan for business reporting, red-flag alerts, production-only notifications, backup discipline, and emergency fallback.

## Operating Principle

Reports tell us what happened.

Red flags tell us what needs attention.

Notifications tell the right person at the right interval.

## Environment Rules

Production is business truth.

Staging is test truth and must be muted.

Development is local truth and must be log-only unless deliberately overridden.

## Notification Policy

Production may send critical alert emails and scheduled digests.

Staging may create alert records and muted notification records, but must not send real email.

Development may create local alert records/logs, but must not send external emails.

## Safety Gates

Do not email raw private scroll text.

Do not email raw seeker conversations.

Do not email secrets, API keys, tokens, or payment credentials.

Do not enable production email until staging has proven muted delivery.

## Core Reports

- Daily business snapshot
- User and tier report
- Login and identity report
- Payment status report
- Collective payments / financial close report
- Usage and cost report
- Oracle quality / dialogue baseline report
- Scroll / corpus health report
- Ingestion job report
- Retrieval performance report
- Admin action / governance report
- Data safety / privacy report
- Deploy report

## Core Alert Areas

- Payment failures
- Provider/cost spikes
- Scroll ingestion failures
- Retrieval/privacy mismatches
- Security and admin events
- Voice failures
- Deployment and migration failures
- Backup and restore failures

## Backup and Fallback

The emergency fallback candidate is the dev machine.

A useful fallback requires:

1. Postgres database backup
2. Uploaded/corpus file backup
3. Environment/secrets restore path

Database alone is not enough because scroll records point to stored files.

## Implementation Order

11.8D.1 — reporting / alerting / backup runbook  
11.8D.2 — report and alert tables migration  
11.8D.3 — report/alert helper functions  
11.8D.4 — muted staging notification delivery  
11.8D.5 — production critical email delivery  
11.8D.6 — daily business snapshot job  

No staging push until the batch is deliberately chosen.
