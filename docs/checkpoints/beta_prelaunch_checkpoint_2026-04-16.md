# Beta Prelaunch Checkpoint - 2026-04-16

## Current branch state
- Branch: `phase8_stripe_integration`
- Staging app is live
- Upload path, billing safety, anonymous continuity, and claim-path UX have all been actively exercised on staging

## Confirmed stable
- Stripe recurring safety guard prevents duplicate recurring checkout for users who already have active recurring support
- Failed recurring renewals no longer rely on grace in the live billing path
- Anonymous continuity works by browser/session path
- Anonymous upload safeguards are in place:
  - 5-second cooldown
  - 3-upload cap
  - claim-required path
- Duplicate uploads are recognized in the seeker path
- Feedback modal flow is working with a single adaptive primary action button
- Claim-path prompts successfully route seekers into account creation

## PDF ingestion state
### Working well now
- Standard text-based PDFs
- Recently published / modern PDFs with readable embedded text
- Difficult but still extractable PDFs via the current fallback path:
  - PyPDF2 first
  - PyMuPDF fallback when needed

### Not suitable for live seeker upload
- Photo-scanned / image-heavy / OCR-poor PDFs
- Very old texts where visible text is too faint, degraded, or effectively image-only

### Current policy
- Keep the current live PDF extraction path for normal PDFs
- Add graceful failure handling for image-heavy / photo-scanned PDFs in the live upload path
- Do not allow raw 502 HTML to surface to seekers
- Return a clear Temple message instead

## Future-state corpus goal
Hard historical scans and archival texts should be preserved for the growing corpus and eventual LLM/domain corpus, but not through the live seeker upload path.

### Planned future lane
- Manual/admin OCR ingestion lane
- Scripted or operator-assisted processing for archival scans
- Corpus preservation workflow distinct from ordinary seeker uploads
- Candidate later path:
  - ingest scan offline
  - OCR/clean text
  - review output
  - add approved text to corpus

## Billing data state
- JD staging subscriptions were reconciled to one active recurring subscription
- Old duplicate recurring subscription was canceled
- Staging now reflects one canonical active recurring subscription for JD

## Still pending before broader beta confidence
- True Stripe change-plan handling
  - one recurring subscription only
  - no proration
  - new cycle starts today
  - immediate upgraded access
- Graceful backend handling for image-heavy / scanned PDFs
- Broader repo/runbook consolidation for beta launch record
- Continued UX polish after billing truth and upload truth are fully locked

## Beta punch-list additions from this tranche
- Add graceful scanned/image-PDF failure handling to live upload path
- Add manual/admin OCR corpus ingestion lane for archival scans
- Document scan-preservation workflow for future corpus growth
