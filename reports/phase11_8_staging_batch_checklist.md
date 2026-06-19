# Phase 11.8 Staging Batch Plan and Migration Checklist

This checklist covers the local Phase 11.8 batch currently above `origin/staging`.

Current local HEAD at checklist creation:

    6b17d2f Reporting: include ingestion queue diagnostics

Current staging baseline before this batch:

    577d079 Retrieval: harden scroll ownership and active filters

Production baseline before this batch:

    6843789 UI: normalize voice page brand headers

Do not treat this checklist as complete until the actual staging SHA and migration results are verified during deployment.

## 1. Batch Contents

The Phase 11.8 local batch includes:

1. Deity-aware retrieval policy
2. Retrieval policy logging
3. Ingestion job queue migration
4. Ingestion job helpers
5. Reporting/alerting runbook baseline
6. Reporting and alert tables
7. Reporting and alert helpers
8. Muted notification helpers
9. Admin reporting diagnostics endpoint
10. Daily business snapshot generator
11. Saved-scroll ingestion helper
12. One-job queued scroll processor
13. Admin queue processor endpoint
14. Queued unreadable PDF preservation
15. Optional large-file queued upload mode, off by default
16. Backup/restore runbook polish
17. Ingestion queue diagnostics in reporting diagnostics

## 2. Required Migration Files

Apply manually in each target environment:

    db/migrations/2026_06_18_phase11_8_ingestion_jobs.sql
    db/migrations/2026_06_18_phase11_8_reporting_alerts.sql

Do not assume local migration state carries into staging or production.

## 3. Pre-Push Local Verification

Run locally before pushing:

    git status --short
    python -m py_compile main.py
    git --no-pager log --oneline origin/staging..HEAD

Confirm:

1. Working tree is clean.
2. `main.py` compiles.
3. The expected local commit stack appears.
4. Upload queue defaults off.
5. No real external email behavior is enabled.
6. The runbook and checklist are committed.

## 4. Staging Push Plan

Recommended route:

1. Push feature branch.
2. Review final diff from `origin/staging`.
3. Fast-forward or merge to staging only after review.
4. Confirm staging service is watching the intended branch.
5. Trigger Render staging deploy.
6. Confirm deployed SHA.

Do not push directly to production.

## 5. Staging Database Backup

Before applying migrations on staging:

1. Confirm you are connected to staging, not local or production.
2. Confirm staging DB identity through safe metadata.
3. Take or confirm a current staging DB backup.
4. Record backup timestamp and method in the chat or deployment notes.

No secret values should be pasted into chat.

## 6. Staging Migration Verification

After applying migrations, verify table existence:

    SELECT to_regclass('public.ingestion_jobs');
    SELECT to_regclass('public.report_artifacts');
    SELECT to_regclass('public.report_runs');
    SELECT to_regclass('public.alert_events');
    SELECT to_regclass('public.notification_deliveries');

Verify basic counts:

    SELECT COUNT(*) FROM ingestion_jobs;
    SELECT COUNT(*) FROM report_artifacts;
    SELECT COUNT(*) FROM report_runs;
    SELECT COUNT(*) FROM alert_events;
    SELECT COUNT(*) FROM notification_deliveries;

Verify ingestion job status view:

    SELECT status, COUNT(*)
    FROM ingestion_jobs
    GROUP BY status
    ORDER BY status;

## 7. Staging Environment Defaults

Initial staging defaults should remain conservative:

    SCROLL_UPLOAD_QUEUE_ENABLED=false
    SCROLL_UPLOAD_QUEUE_MIN_BYTES=500000

Reporting/alerting should remain muted/log-only unless deliberately changed:

    ALERT_EMAILS_ENABLED=false
    ALERT_EMAIL_MODE=muted
    ALLOW_EXTERNAL_EMAILS=false

If staging uses different names, verify behavior through diagnostics, not memory.

## 8. Staging Smoke Tests

After deploy and migrations:

1. Confirm `/temple` loads.
2. Confirm text Oracle path still answers.
3. Confirm normal small scroll upload still works synchronously.
4. Confirm scroll counter or upload confirmation still behaves.
5. Confirm `/admin/reports/reporting-diagnostics` works for admin.
6. Confirm diagnostics show upload queue settings.
7. Confirm diagnostics show ingestion job counts.
8. Confirm `/admin/reports/daily-business-snapshot` can create a snapshot if admin-authenticated.
9. Confirm `/admin/ingestion/process-one-scroll` returns no queued jobs when queue is empty.
10. Confirm no external email is sent from staging.

Optional controlled queue test on staging only after basic smoke passes:

1. Temporarily enable queue mode only if desired.
2. Upload a large test file.
3. Confirm 202 queued response.
4. Confirm ingestion_jobs row appears.
5. Run admin process-one endpoint.
6. Confirm job becomes ready or needs_ocr.
7. Disable queue mode again unless explicitly accepted.

## 9. Production Promotion Gate

Do not promote to production until:

1. Staging deploy SHA is confirmed.
2. Staging migrations are confirmed.
3. Staging smoke tests pass.
4. Queue mode remains off by default.
5. Reporting emails remain controlled.
6. Upload behavior is accepted.
7. Retrieval behavior is accepted.
8. A production backup plan is confirmed.

Production database migrations must be applied and verified separately.

## 10. Rollback Notes

For code-only issues:

1. Roll staging back to last accepted SHA.
2. Preserve logs.
3. Do not run further migrations until root cause is understood.

For migration/data issues:

1. Stop additional writes if needed.
2. Preserve logs and failing examples.
3. Decide whether forward fix or restore is safer.
4. Favor data preservation over speed.

## 11. Current Acceptance Summary

This Phase 11.8 batch is designed to be conservative:

1. Retrieval is more explicit and better logged.
2. Reporting/alerting tables are added but do not send external email by default.
3. Ingestion queue exists but is not automatic.
4. Large-file queue mode is off by default.
5. Live small-upload behavior remains the default seeker experience.
6. Queued unreadable PDFs preserve source files for later OCR/admin handling.
