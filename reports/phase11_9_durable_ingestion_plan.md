# Phase 11.9 Durable Ingestion Plan

Goal:
Prepare God Incorporated ingestion for Apple/public launch by making uploaded scroll processing durable, recoverable, observable, and worker-ready.

Current production baseline:
- Upload queue enabled.
- Auto processor enabled.
- Background web-service processor max jobs per upload.
- ingestion_jobs table active.
- Production accepted on commit 888e899.

Final requirements:
1. Durable uploaded-file storage.
2. Dedicated worker or scheduled processor.
3. Stale processing recovery.
4. Retry and failure policy.
5. OCR-needed lane.
6. Admin ingestion dashboard.
7. Backup/reprocessing runbook.
8. Load/cost acceptance before Apple launch.

Decision needed:
Choose object storage or Render persistent disk before worker implementation.
