# Phase 11.8 Reporting, Alerting, Backup, and Restore Runbook

This runbook records the Phase 11.8 operating plan for business reporting, red-flag alerts, backup discipline, restore confidence, and emergency fallback.

Status: local development baseline. Do not treat this document as evidence that staging or production has been migrated until each environment has been manually verified.

## 1. Operating Principles

God Incorporated has three independent lanes: local development, Render staging, and production.

Database migrations do not automatically propagate between environments. Each database must be migrated and verified independently.

Deployment discipline remains: feature/dev branch, staging branch and Render staging deploy, staging acceptance, then production promotion through beta_launch_prod_cutover.

## 2. Backup Scope

A complete operational backup must cover more than code.

Required backup surfaces include the Git repository and branch/tag state, PostgreSQL database, uploaded scroll/source files, reporting and alert tables, environment variable inventory without exposing secrets, Render service configuration, Stripe/customer/payment state when payment work is in scope, and Apple/iOS release state when mobile release work is in scope.

The code repository alone is not a full backup.

## 3. PostgreSQL Backup Baseline

Use pg_dump for logical database backups.

Recommended local pattern:

    mkdir -p local_backups/db

    pg_dump --format=custom --no-owner --no-acl \
      --file="local_backups/db/godinc_dev_$(date +%Y%m%d-%H%M%S).dump" \
      "$DATABASE_URL"

For staging and production, take backups from the correct environment or provider console. Never assume the local database matches staging or production.

Before applying migrations in staging or production: confirm the target environment, confirm the current Git SHA, confirm the target database through safe metadata without exposing credentials, take or confirm a current backup, apply migrations, verify table/index existence and row counts, and run smoke tests.

## 4. Restore Baseline

Restores should be rehearsed before they are needed.

Recommended local restore rehearsal pattern:

    createdb godinc_restore_test

    pg_restore --dbname=godinc_restore_test --no-owner --no-acl \
      local_backups/db/<backup_file>.dump

After restore, verify row counts for users, scrolls, scroll_chunks, ingestion_jobs, report_artifacts, report_runs, alert_events, and notification_deliveries.

A backup is not considered proven until at least one restore rehearsal has completed successfully.

## 5. Uploaded Scroll File Backup

Uploaded files are part of the seeker corpus and must be backed up alongside the database.

Minimum checks: confirm upload directory path for each environment, confirm file count, confirm total size, confirm that scrolls.storage_ref values point to real files where applicable, and confirm unreadable queued PDFs marked needs_ocr still retain source files.

Do not delete uploaded source files unless the database row and intended lifecycle are understood.

## 6. Ingestion Queue Backup Notes

Phase 11.8 introduced an ingestion queue lane.

Relevant table: ingestion_jobs.

Important statuses: queued, processing, ready, failed, needs_ocr.

The queue path is conservative: queue mode is off by default, large-file queue mode only activates when explicitly enabled, small files remain synchronous by default, and queued unreadable PDFs preserve the original file and become needs_ocr.

Operational check:

    SELECT status, COUNT(*)
    FROM ingestion_jobs
    GROUP BY status
    ORDER BY status;

A large number of queued or processing rows means ingestion is falling behind. A large number of needs_ocr rows means the OCR/admin handling lane needs attention.

## 7. Reporting and Alert Tables

Phase 11.8 reporting/alerting tables: report_artifacts, report_runs, alert_events, and notification_deliveries.

Admin diagnostic route: GET /admin/reports/reporting-diagnostics.

Daily business snapshot route: POST /admin/reports/daily-business-snapshot.

Reporting tables are operational records. They should be backed up with the database, but they should not contain private seeker conversation payloads unless explicitly designed and reviewed.

## 8. Notification Safety

Default rule: development is muted/log-only, staging is muted/log-only, and production is critical-only email when explicitly enabled.

Suggested environment controls: ALERTS_ENABLED, ALERT_EMAILS_ENABLED, ALERT_EMAIL_MODE, ADMIN_ALERT_EMAILS, REPORTS_FROM_EMAIL, ALERTS_FROM_EMAIL, and ALLOW_EXTERNAL_EMAILS.

Do not enable broad external notification behavior in staging or development.

## 9. Red-Flag Alert Categories

Critical alert candidates include payment webhook failure, subscription/entitlement mismatch, database write failure, upload/ingestion failure spike, repeated queued-job failures, backup failure, restore rehearsal failure, admin route authorization anomaly, excessive API usage/cost spike, and privacy/security anomaly.

Warnings should usually remain dashboard/report items unless repeated or severe.

## 10. Emergency Fallback

If staging or production breaks after deployment: stop and identify current Git SHA, confirm whether the issue is code/database/environment/external provider, avoid additional migrations until the failure class is known, roll back code-only failures to the last accepted SHA, handle migration-related failures with backup/restore or forward fix after review, preserve logs and failing request examples, and record the incident in the reporting/alerting lane.

Production fallback must favor data preservation over speed.

## 11. Pre-Staging Checklist for Phase 11.8 Batch

Before pushing the local Phase 11.8 stack to staging:

1. Confirm clean working tree.
2. Confirm local HEAD.
3. Confirm migrations present:
   - 2026_06_18_phase11_8_ingestion_jobs.sql
   - 2026_06_18_phase11_8_reporting_alerts.sql
4. Confirm local migrations applied.
5. Confirm main.py compiles.
6. Confirm upload queue defaults off.
7. Confirm no real external email is enabled.
8. Confirm staging backup exists or can be taken.
9. Push feature branch.
10. Promote to staging branch only after review.
11. Apply staging migrations manually.
12. Deploy staging.
13. Run smoke tests.
14. Accept staging before production promotion.

## 12. Current Phase 11.8 Local Status

Completed locally above current staging:

1. Deity-aware retrieval policy
2. Retrieval policy logging
3. Ingestion job migration
4. Ingestion job helpers
5. Reporting/alert table migration
6. Reporting/alert helpers
7. Muted notification helpers
8. Admin reporting diagnostics endpoint
9. Daily business snapshot generator
10. Saved-scroll ingestion helper
11. One-job queued scroll processor
12. Admin manual queue processor endpoint
13. Queued unreadable PDF preservation
14. Optional large-file queued upload mode, off by default

Staging has not yet received this full batch.

Production has not yet received this full batch.
