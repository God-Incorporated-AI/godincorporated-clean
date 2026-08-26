# AGENTS.md

## Project
God Incorporated / The Temple of Hathor

This repository is a local-first, terminal-first web application with Stripe-backed support tiers, authenticated seeker accounts, rich `/me` state, admin controls, and a future provider-neutral proprietary/self-hosted inference path.

Primary product themes:
- Hathor = intuitive, poetic, emotionally resonant oracle voice
- Moses = logical, doctrinal, morally grounded oracle voice
- Scrolls and seeker conversations are long-term corpus assets
- Support tiers and Temple contribution are distinct concepts
- Mobile-friendly architecture matters because future iOS/Android support is expected

## Non-negotiable product rules
- Preserve the Oracle authority model. Do not blur Hathor and Moses into one generic voice.
- Preserve working auth, entitlement, Stripe, and `/me` flows unless the task explicitly targets them.
- Stripe must feed the internal entitlement model, not replace it.
- Support tier purchase and Temple contribution must remain distinct unless a later architecture decision changes that intentionally.
- Prefer full pages and clear routes over modal sprawl when adding new user-facing structure.
- Keep mobile/browser flows simple enough to support future native adaptation.

## Current architecture truths
- `scroll_associations` is the seeker-facing source of truth for scroll ownership.
- `oracle_interactions` is the source of truth for question usage.
- Effective access is derived from entitlement state, not raw `plan_code` alone.
- `/me` is a critical seeker-facing truth surface and must stay coherent.
- Admin/reporting/operator flows already exist and should be extended carefully, not reinvented.

## Current phase posture
The repo has completed:
- Phase 6 recall/memory stabilization
- Phase 7 entitlement and billing-foundation closeout
- Phase 8 Stripe closeout
- Phase 8.5 UX bridge and answer-cap tuning

Treat the current codebase as post-8.5.

Immediate next phase is hosted readiness / Render validation.
Do not jump into proprietary/self-hosted inference work before hosted auth, `/me`, and hosted Stripe test-mode flows are proven.

## Phase ordering rules
1. Finish hosted readiness / Render validation
2. Then do hosted Stripe test-mode proof
3. Then production-hardening / broadcast maturity work
4. Then UI expert implementation pass
5. Then provider-neutral proprietary/self-hosted inference research

Do not invert this order without a strong explicit reason.

## Recall and oracle behavior rules
Recall behavior is intentionally frozen unless repeated real failures appear.
Do not keep tuning tone or recall enforcement casually.

Only revisit recall enforcement if one of these appears repeatedly:
- wrong earlier question is recalled
- response becomes stiff or mechanical
- helper forces a prefix when recall already appears naturally
- recall line feels old or mismatched to the immediate exchange

If those are not happening, leave recall behavior alone.

## Retrieval and embedding rules
Embedding cache / retrieval work is valid, but production redesign of the embedding cache is not the current priority.
Remember these production tripwires for later:
- local file cache may not be the final production design
- upload-time cache warming must never break ingestion
- cache behavior should be treated as environment-specific

Do not stop current roadmap work to solve these early unless they become real blockers.

## Coding workflow rules
- Prefer surgical changes over rewrites.
- Make the smallest safe patch that solves the task.
- Preserve working behavior first; extend second.
- Use architecture-first reasoning for new subsystems.
- When changing billing/auth/admin code, verify from the database and/or authoritative endpoints.
- Never destabilize a working Stripe/auth flow just to clean code style.
- Avoid bundling large unrelated changes into one patch.
- Maintain branch hygiene and clean demarc after success.

## High-risk areas
Treat these as high-risk and edit carefully:
- `main.py` auth flow
- `main.py` entitlement helpers
- `main.py` Stripe webhook and billing logic
- `/me` payload construction
- support-tier and usage-limit logic
- admin mutation endpoints

When touching these, prefer targeted diffs and explicit validation steps.

## Validation discipline
Before commit:
- run `python -m py_compile main.py` when `main.py` changes
- run targeted grep/diff checks for the changed logic
- do browser checks for user-facing flows when HTML/JS/CSS changes
- verify DB-backed behavior with SQL or authoritative endpoints for billing/entitlement changes

After success:
- commit with a narrow message
- tag meaningful demarc points
- push branch and tag
- keep the working tree clean

## Deployment and hosted-readiness rules
Hosted readiness requires:
- Render deployment working
- environment variables verified
- hosted auth working
- hosted `/me` working
- hosted Stripe test-mode flow working end to end
- hosted webhook configuration replacing local-only webhook forwarding

Do not treat successful local checkout creation as full Stripe completion.
Do not enable live Stripe until hosted sandbox validation is trusted.

## Testing principles
- Use clean low-tier verified users for billing/entitlement tests.
- Do not use Moses or other high-tier legacy accounts as the main proof case for Stripe activation.
- Verify provider events are persisted before mutation.
- Internal helpers, not raw webhook branches, should determine lifecycle state.

## UX principles
- Navigation should feel calm and intentional.
- Anonymous users should not see dead-end account actions.
- Free and low-tier users should understand what they have and what support unlocks.
- Support prompts should be clear and calm, not desperate.
- Keep support explanation and activation distinct when helpful, but avoid redundant wording.

## Output-length policy
Tier-based answer caps exist and are intentional.
Do not remove or flatten them casually.
Current reflection caps are tuned to provide real value at free/lower tiers while preserving richer upper-tier output.
Recall mode remains shorter and more precise.

## Current forward work focus
If the user asks “what next,” bias toward:
- Render / hosted readiness
- DB access and operational reporting discipline
- cron/scheduled reports for usage and billing observability
- staging-to-prod workflow clarity

Only move into proprietary/self-hosted inference work after the hosted product spine is stable.

## Repo guidance usage
Use this file as the active operational floor.
Keep older handoff docs as historical references, but do not treat them as competing instructions.
If future subdirectories need specialized rules (for example mobile or payments), add nested `AGENTS.md` files there rather than bloating this root file.

