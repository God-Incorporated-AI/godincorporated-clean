# Staging Stripe Billing and Upload Recovery Runbook

## Purpose
This runbook records the current staging recovery and validation posture for:
- Stripe recurring billing safety
- seeker upload stability
- anonymous continuity / claim flow
- scanned-PDF handling policy

---

## 1. Stripe billing safety posture

### Current rule
Only one active recurring Stripe subscription should exist per user.

### Current staging behavior
- If a user already has active recurring support, the app blocks opening a second recurring checkout session.
- This is a safety guard until true change-plan handling is implemented.

### JD staging note
JD was used as a billing lifecycle test user and previously accumulated duplicate recurring subscriptions.
Current staging state should preserve only one canonical active recurring subscription.

### Recovery pattern
1. Verify active Stripe subscriptions directly from Render shell using `STRIPE_SECRET_KEY`
2. Cancel stale duplicate recurring subscriptions in Stripe
3. Confirm local `subscriptions` table reflects one active recurring row
4. Confirm `/me` still reflects the canonical plan and renewal state

### Future-state goal
Replace the temporary blocker with true change-plan handling:
- one recurring subscription only
- no proration
- new billing cycle starts today
- immediate upgraded access

---

## 2. Anonymous continuity and claim-path posture

### Current rule
Anonymous seekers may begin a browser-based path before creating an account.

### Current protections
- browser/session continuity path
- 5-second anonymous upload cooldown
- 3-upload anonymous cap
- claim-required messaging
- single adaptive modal action guiding account creation

### Expected behavior
- duplicates are recognized in the seeker path
- claim messaging escalates with repeated anonymous uploads
- account creation can be launched directly from the claim modal flow

---

## 3. PDF upload handling posture

### Working live path
Use ordinary live seeker upload for:
- text-based PDFs
- modern PDFs with embedded text
- difficult but still extractable PDFs through the current fallback chain

### Current extraction posture
- PyPDF2 first
- PyMuPDF fallback for difficult PDFs

### Do not rely on live seeker upload for
- photo-scanned PDFs
- image-heavy archival texts
- faint or degraded scans of old books/manuscripts
- OCR-poor historical documents

### Immediate live-path policy
If a PDF is image-heavy or effectively unreadable by the live text extraction path:
- fail gracefully
- show a clean Temple error
- do not surface raw HTML 502 responses
- direct the user toward text-based or OCR-processed uploads

### Future-state goal
Create a separate manual/admin OCR ingestion lane for archival corpus growth:
- preserve hard scans
- OCR offline or in an operator workflow
- review and clean extracted text
- ingest approved output into corpus intentionally

---

## 4. Upload failure handling policy

### Current requirement
The seeker should never see raw HTML error pages in the modal.

### Desired user-facing failure language
Example:
"This scroll appears to be image-based or photo-scanned. The Temple could not reliably read it through the live upload path. Please upload a text-based PDF, TXT, DOCX, or an OCR-processed scan."

### Escalation
If scan ingestion is important for corpus growth:
- route it into the future admin/manual OCR lane
- do not expand the live request timeout path

---

## 5. Beta recordkeeping posture

Before broader beta polish, record each hardening tranche in repo docs:
- what was stabilized
- what was intentionally deferred
- what belongs to future corpus/LLM workflows
- what is safe for live seeker use now

This runbook and the current beta checkpoint doc should be updated whenever billing truth, upload truth, or corpus-ingestion policy changes.
