BEGIN;

-- R6D: Aggregate Temple Context foundation.
--
-- Privacy boundary:
--   * These tables contain aggregate/deidentified semantic state only.
--   * No seeker, anonymous identity, session, interaction, filename,
--     storage, or source-row identifiers may be stored here.
--   * No raw question/response text or quotations may be stored here.
--
-- Runtime boundary:
--   * R6D begins in shadow mode.
--   * Nothing in this migration changes Oracle prompt construction,
--     retrieval, persona, inference routing, or private seeker memory.


DO $$
BEGIN
    IF to_regclass('public.temple_context_themes') IS NOT NULL THEN
        RAISE EXCEPTION
            'STOP: temple_context_themes already exists; inspect first';
    END IF;

    IF to_regclass('public.temple_context_theme_daily') IS NOT NULL THEN
        RAISE EXCEPTION
            'STOP: temple_context_theme_daily already exists; inspect first';
    END IF;
END
$$;


CREATE TABLE public.temple_context_themes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Short deidentified semantic representation.
    -- Never raw seeker language or a quotation.
    theme_label text NOT NULL,
    theme_summary text NOT NULL,

    -- Hash of the normalized aggregate theme representation.
    -- This is not a hash of a seeker interaction or source document.
    theme_hash text NOT NULL,

    theme_embedding vector(1536),
    theme_embedding_model text,

    -- Aggregate statistics only.
    observation_count bigint NOT NULL DEFAULT 0,
    contributor_count bigint NOT NULL DEFAULT 0,

    -- Previous equal-length comparison window.
    -- Distinct contributor counts are calculated upstream while
    -- identity is transient, then only these aggregate values remain.
    prior_observation_count bigint NOT NULL DEFAULT 0,
    prior_contributor_count bigint NOT NULL DEFAULT 0,

    prior_window_started_at timestamptz,
    prior_window_ended_at timestamptz,

    recent_observation_count bigint NOT NULL DEFAULT 0,
    recent_contributor_count bigint NOT NULL DEFAULT 0,

    -- Explicit current window used for the momentum snapshot.
    recent_window_started_at timestamptz,
    recent_window_ended_at timestamptz,

    -- momentum_score represents current aggregate attention.
    -- acceleration_score may be positive or negative.
    momentum_score double precision NOT NULL DEFAULT 0,
    acceleration_score double precision NOT NULL DEFAULT 0,

    first_observed_at timestamptz,
    last_observed_at timestamptz,
    last_computed_at timestamptz,

    deidentification_version text NOT NULL,
    aggregation_version text NOT NULL,

    -- shadow:
    --   calculated but never supplied to Oracle inference.
    --
    -- suppressed:
    --   insufficient cohort/privacy/relevance strength.
    --
    -- eligible:
    --   may become eligible for future prompt gating only after
    --   separate acceptance work.
    --
    -- retired:
    --   no longer active.
    status text NOT NULL DEFAULT 'shadow',

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT temple_context_themes_label_check
        CHECK (length(btrim(theme_label)) > 0),

    CONSTRAINT temple_context_themes_summary_check
        CHECK (length(btrim(theme_summary)) > 0),

    CONSTRAINT temple_context_themes_hash_check
        CHECK (theme_hash ~ '^[0-9a-f]{64}$'),

    CONSTRAINT temple_context_themes_hash_unique
        UNIQUE (theme_hash),

    CONSTRAINT temple_context_themes_embedding_pair_check
        CHECK (
            (
                theme_embedding IS NULL
                AND theme_embedding_model IS NULL
            )
            OR
            (
                theme_embedding IS NOT NULL
                AND theme_embedding_model IS NOT NULL
                AND length(btrim(theme_embedding_model)) > 0
            )
        ),

    CONSTRAINT temple_context_themes_observation_count_check
        CHECK (observation_count >= 0),

    CONSTRAINT temple_context_themes_contributor_count_check
        CHECK (
            contributor_count >= 0
            AND contributor_count <= observation_count
        ),

    CONSTRAINT temple_context_themes_prior_observation_check
        CHECK (
            prior_observation_count >= 0
            AND prior_observation_count <= observation_count
        ),

    CONSTRAINT temple_context_themes_prior_contributor_check
        CHECK (
            prior_contributor_count >= 0
            AND prior_contributor_count <= contributor_count
            AND prior_contributor_count <= prior_observation_count
        ),

    CONSTRAINT temple_context_themes_recent_observation_check
        CHECK (
            recent_observation_count >= 0
            AND recent_observation_count <= observation_count
        ),

    CONSTRAINT temple_context_themes_recent_contributor_check
        CHECK (
            recent_contributor_count >= 0
            AND recent_contributor_count <= contributor_count
            AND recent_contributor_count <= recent_observation_count
        ),

    CONSTRAINT temple_context_themes_comparison_window_check
        CHECK (
            (
                prior_window_started_at IS NULL
                AND prior_window_ended_at IS NULL
                AND recent_window_started_at IS NULL
                AND recent_window_ended_at IS NULL
            )
            OR
            (
                prior_window_started_at IS NOT NULL
                AND prior_window_ended_at IS NOT NULL
                AND recent_window_started_at IS NOT NULL
                AND recent_window_ended_at IS NOT NULL
                AND prior_window_started_at
                    < prior_window_ended_at
                AND prior_window_ended_at
                    = recent_window_started_at
                AND recent_window_started_at
                    < recent_window_ended_at
                AND (
                    prior_window_ended_at
                    - prior_window_started_at
                ) = (
                    recent_window_ended_at
                    - recent_window_started_at
                )
            )
        ),

    CONSTRAINT temple_context_themes_momentum_check
        CHECK (momentum_score >= 0),

    CONSTRAINT temple_context_themes_time_check
        CHECK (
            (
                first_observed_at IS NULL
                AND last_observed_at IS NULL
            )
            OR
            (
                first_observed_at IS NOT NULL
                AND last_observed_at IS NOT NULL
                AND first_observed_at <= last_observed_at
            )
        ),

    CONSTRAINT temple_context_themes_deidentification_check
        CHECK (
            length(btrim(deidentification_version)) > 0
        ),

    CONSTRAINT temple_context_themes_aggregation_check
        CHECK (
            length(btrim(aggregation_version)) > 0
        ),

    CONSTRAINT temple_context_themes_status_check
        CHECK (
            status IN (
                'shadow',
                'suppressed',
                'eligible',
                'retired'
            )
        )
);


CREATE TABLE public.temple_context_theme_daily (
    theme_id uuid NOT NULL
        REFERENCES public.temple_context_themes(id)
        ON DELETE CASCADE,

    bucket_date date NOT NULL,

    -- Aggregate values only.
    -- Contributor identities are calculated upstream and discarded.
    observation_count bigint NOT NULL DEFAULT 0,
    contributor_count bigint NOT NULL DEFAULT 0,

    aggregation_version text NOT NULL,

    computed_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (
        theme_id,
        bucket_date
    ),

    CONSTRAINT temple_context_theme_daily_observation_check
        CHECK (observation_count >= 0),

    CONSTRAINT temple_context_theme_daily_contributor_check
        CHECK (
            contributor_count >= 0
            AND contributor_count <= observation_count
        ),

    CONSTRAINT temple_context_theme_daily_aggregation_check
        CHECK (
            length(btrim(aggregation_version)) > 0
        )
);


CREATE INDEX idx_temple_context_themes_status_momentum
ON public.temple_context_themes (
    status,
    momentum_score DESC,
    last_observed_at DESC
);


CREATE INDEX idx_temple_context_themes_last_observed
ON public.temple_context_themes (
    last_observed_at DESC
);


CREATE INDEX idx_temple_context_themes_embedding_hnsw_cosine
ON public.temple_context_themes
USING hnsw (
    theme_embedding vector_cosine_ops
)
WHERE theme_embedding IS NOT NULL;


CREATE INDEX idx_temple_context_theme_daily_bucket
ON public.temple_context_theme_daily (
    bucket_date DESC
);


COMMENT ON TABLE public.temple_context_themes IS
'R6D deidentified aggregate Temple Context themes. Contains no seeker, session, interaction, source-row, filename, storage, or raw dialogue provenance. Shadow-only foundation until separately accepted for inference use.';


COMMENT ON TABLE public.temple_context_theme_daily IS
'R6D aggregate daily theme statistics used to calculate collective attention and acceleration. Contributor identities are not retained in this table.';


COMMENT ON COLUMN public.temple_context_themes.contributor_count IS
'Snapshot of distinct eligible contributors at the last aggregate computation. This value is recomputed upstream and is never incremented as an additive counter. No contributing identity or reversible backpointer is retained.';


COMMENT ON COLUMN public.temple_context_themes.prior_contributor_count IS
'Snapshot of distinct eligible contributors inside the immediately preceding equal-length comparison window. Identity is discarded after this count is calculated.';


COMMENT ON COLUMN public.temple_context_themes.recent_contributor_count IS
'Snapshot of distinct eligible contributors inside the explicit recent comparison window. Momentum frequency and acceleration use contributor breadth rather than raw question volume so one prolific seeker cannot dominate the signal.';


COMMENT ON COLUMN public.temple_context_theme_daily.contributor_count IS
'Distinct eligible contributors observed within this bucket_date only. Counts from separate days must not be summed to infer a cross-day distinct-contributor total.';


COMMENT ON COLUMN public.temple_context_themes.theme_summary IS
'Deidentified semantic abstraction only; must not contain raw seeker dialogue or quotations.';


COMMIT;
