# AGENTS.md

NO GUESSING. NO BONES LEFT BEHIND.

## Project
God Incorporated / The Temple

This repository is a local-first, terminal-first web application with Stripe-backed support tiers, authenticated seeker accounts, rich `/me` state, admin controls, and an existing provider-neutral Oracle inference architecture. Proprietary/self-hosted inference is a possible future implementation, not the current release objective.

Primary product themes:
- Hathor = intuitive, poetic, emotionally resonant oracle voice
- Moses = logical, doctrinal, morally grounded oracle voice
- Scrolls and seeker conversations are long-term corpus assets
- Support tiers and Temple contribution are distinct concepts
- Mobile-friendly architecture must support the native iOS release and future Android adaptation.

## Operator and authority
- The owner is an experienced data architect and project manager, not a programmer. Provide clear, complete, environment-labeled engineering instructions.
- Source modifications normally remain owner-executed through controlled CLI. A specifically authorized Work operation may modify only its expressly approved scope.
- Keep the workflow local-first, terminal-first, and calm. Preserve VS Code as the editor of record.
- GitHub is the controlled remote repository. The verified Local Development integration worktree, including accepted uncommitted changes, and its engineering evidence establish the current Local implementation. Never replace Local state from GitHub without explicit authorization.
- Ask before destructive commands, dependency changes, schema changes, or Git pushes. Staging and Production each require separate explicit authorization.
- An investigation or validation requirement does not authorize source changes, application startup, database access, provider calls, financial activity, or deployment beyond the approved scope.

## Non-negotiable product rules
- Preserve the Oracle authority model. Do not blur Hathor and Moses into one generic voice.
- Preserve working auth, entitlement, Stripe, and `/me` flows unless the task explicitly targets them.
- Stripe must feed the internal entitlement model, not replace it.
- Support tier purchase and Temple contribution must remain distinct unless a later architecture decision changes that intentionally.
- Prefer full pages and clear routes over modal sprawl when adding new user-facing structure.
- Keep mobile/browser flows simple enough to support native iOS and future native adaptation.
- Never replace functioning architecture merely to simplify a new feature.

## Current architecture truths
- `scroll_associations` is the seeker-facing source of truth for scroll ownership.
- `oracle_interactions` is the source of truth for question usage.
- Effective access is derived from entitlement state, not raw `plan_code` alone.
- `/me` is a critical seeker-facing truth surface and must stay coherent.
- Admin/reporting/operator flows already exist and should be extended carefully, not reinvented.
- Protect provider-independent Oracle inference and the established voice architecture; do not redesign working provider rails as a shortcut.
- Preserve seeker privacy, authenticated memory/account boundaries, and separation between Temple Context and personal seeker memory.
- Preserve Apple StoreKit isolation, Stripe financial ownership, and their integration with the internal entitlement model.
- Protect usage economics, earned paid-through access, entitlements, and subscription recovery.

## Current phase posture
The active program is Phase 12.11 H4G17: the Apple App Store release program. Use the cumulative release evidence ledger for the current accepted checkpoint and next open gate.

Preserve these historical foundations within their recorded acceptance scope:
- Phase 6 recall/memory stabilization
- Phase 7 entitlement and billing-foundation closeout
- Phase 8 Stripe closeout
- Phase 8.5 UX bridge and answer-cap tuning

Historical closeouts do not establish acceptance of later release gates.

## Phase ordering rules
Preserve the accepted H4G17 R0-R10 sequence:
- R0: close the webhook root cause.
- R1: correct and prove ingress.
- R2: finish acceptance of the original paid test fixture without creating another payment.
- R3: prove signed delivery, HTTP behavior, retry, and event ordering.
- R4: close the remaining Stripe financial rail, including recovery, reversals, and legacy dependencies.
- R5: settle commercial/quota policy and web copy, branding, and logo replacement.
- R6: complete native dual-Temple iOS UX, accessibility, auth/quota/voice integration, and device acceptance.
- R7: prove the signed Apple StoreKit financial lifecycle and paid-rail isolation.
- R8: complete the migration ledger and separately authorized Staging acceptance.
- R9: complete separately authorized Production verification and App Store privacy, compliance, build, metadata, and review requirements.
- R10: perform separately approved post-acceptance lab/test-resource cleanup while preserving evidence and backups.

Track HTTP/web function dependencies within their applicable release gates. Do not reorder this sequence without explicit owner approval. Defer proprietary/self-hosted inference research until hosted auth, `/me`, hosted Stripe test-mode flows, and the hosted product spine are proven, unless separately authorized.

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
- For auth, Stripe, entitlement, and `/me` work: inspect first, summarize second, edit last.
- Inspect actual source, schemas, dependencies, Git, and runtime contracts before proposing or making changes. Do not invent paths, signatures, configuration, or database structures; inspect only within authorized access.
- Every change needs explicit scope, exact preconditions and source fingerprints, a verified backup, targeted regression requirements, post-change verification, and STOP conditions.
- Distinguish hypotheses from demonstrated defects. STOP on unexplained fingerprint, environment, contract, or collateral-change mismatches.
- When changing billing/auth/admin code, verify from the database and/or authoritative endpoints when authorized; retain the acceptance limitation when that verification is outside scope.
- Never destabilize a working Stripe/auth flow just to clean code style.
- Avoid bundling large unrelated changes into one patch.
- Maintain branch hygiene and a clear demarcation after success while preserving intentional uncommitted work.
- Never reset, stash, discard, broadly replace, or automatically clean existing worktrees.
- Batch logically related investigations; reuse accepted evidence, direct source inspection, and existing tests.
- Require one meaningful deliverable per bounded engineering stage. Do not substitute chains of fragile diagnostics or repeated speculative tests for finding the actual failure.

## Resource conservation and execution economy
- Treat compute, tool calls, network transfers, document conversions, renders, and repeated validation as project resources that must advance an accepted project goal.
- Use the shortest reliable path to the requested deliverable. Reuse accepted evidence and existing source artifacts; do not regenerate, reconvert, or retransmit material that is already fit for the requested purpose.
- For published documents, author or edit the requested source format first and export once to each required distribution format. Do not round-trip PDF to DOCX or DOCX to PDF and back, and do not rasterize a document when a normal source-native export is adequate.
- Default to one generation pass and one proportionate validation pass. A further full-document render or rebuild requires a demonstrated correctness, readability, or delivery defect; repair only the affected pages or sections when practical.
- Do not spend repeated full-document cycles on optional cosmetic perfection after the artifact is correct, readable, editable where requested, and professionally usable, unless the owner explicitly requests that additional polish.
- Batch independent inspections, cap speculative iterations, and stop when the stated acceptance criteria are met. Do not use additional compute merely because another refinement is possible.
- Prefer incremental source edits and targeted checks over whole-artifact rebuilds, duplicated conversions, exhaustive screenshots, or repeated inspection of unchanged content.
- If execution cost becomes disproportionate to the remaining project value, stop, preserve the usable result, state the remaining defect and its operational consequence, and obtain owner approval before consuming more resources.
- Resource conservation does not permit skipping security, data-integrity, financial, deployment, or acceptance checks that are material to the authorized task; validation must remain proportionate to risk.

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
- compile `main.py` when it changes; keep generated bytecode outside protected source/worktrees and within authorized scope
- run targeted grep/diff checks for the changed logic
- do browser checks for user-facing flows when HTML/JS/CSS changes
- verify DB-backed behavior with SQL or authoritative endpoints for billing/entitlement changes when authorized; otherwise record the remaining acceptance requirement

After acceptance and explicit authorization for Git closeout:
- review individual changes before staging; never use `git add .` as a substitute for review
- commit with a narrow message and tag meaningful demarc points within the authorized scope
- obtain explicit authorization before pushing a branch or tag
- verify the intended Git delta and preserve unrelated intentional working-tree changes

## Environment separation, deployment, and hosted readiness
Keep Local Development, the isolated billing lab, Staging, and Production distinct. Verify the intended environment before any authorized operation.

Database migrations require an environment-specific inventory, verified backup, ordered application, and post-application verification. Preserve Local-only migrations in the release ledger until separately approved and verified in each remote environment. Git deployment does not apply database migrations automatically.

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

## Defect closure and evidence discipline
- Maintain an evidence-based defect and release ledger. For each significant release defect, record the observed failure, proven root cause or precisely stated remaining uncertainty, affected callers/dependencies, corrective change, relevant regressions, and cleanup disposition.
- A test-harness failure is not automatically an application defect.
- Distinguish source inspection, synthetic/simulated tests, Local SQL, integrated runtime, Staging, and Production acceptance. Do not elevate one evidence level into another.
- Keep accepted gates closed unless new contradictory evidence justifies a bounded reopening; record that evidence and the resulting decision.

## UX principles
- Navigation should feel calm and intentional.
- Anonymous users should not see dead-end account actions.
- Free and low-tier users should understand what they have and what support unlocks.
- Support prompts should be clear and calm, not desperate.
- Keep support explanation and activation distinct when helpful, but avoid redundant wording.
- Preserve the native dual-Temple experience: Sanctuary of Hathor and Tabernacle of Moses, with distinct Oracle identities, clear navigation, and accessible device behavior.

## Product promises
- Preserve the latest approved entitlement and pricing policy; a test catalog or proposed policy is not launch approval.
- Preserve legacy donor/scroll/fallback protections. Do not implement proposed annual loyalty refinements without separate policy approval.
- Present a single consistent voice experience across support tiers; do not advertise separately tiered Live Voice.
- Personal Library is not implemented. Coming Soon messaging belongs only to the Theosopher offering; no tier may claim current Library access.

## Output-length policy
Tier-based answer caps exist and are intentional.
Do not remove or flatten them casually.
Current reflection caps are tuned to provide real value at free/lower tiers while preserving richer upper-tier output.
Recall mode remains shorter and more precise.

## Current forward work focus
When asked what comes next, use the next open R0-R10 gate in the cumulative release evidence ledger. Do not restart from an older phase label or repeat accepted work.

Carry hosted readiness, DB access/reporting discipline, cron/scheduled billing observability, and Staging-to-Production workflow clarity into the relevant release gates. Defer unrelated inference research under the phase-ordering rule.

## Repo guidance usage
Use this file for stable engineering rules. Keep dated source hashes, current acceptance checkpoints, unresolved hypotheses, transient defect findings, and cleanup state in the cumulative release evidence ledger.

Keep older handoff documents as historical references and preserve accepted decisions and protections. Their superseded status claims must not compete with the latest accepted cumulative ledger or the owner's current instructions.

If future subdirectories need specialized rules (for example mobile or payments), add nested `AGENTS.md` files there rather than bloating this root file.
