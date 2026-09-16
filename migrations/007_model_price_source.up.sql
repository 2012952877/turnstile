-- Official list price + discount.
--
-- The four existing managed_model.*_cost_per_million columns keep their meaning: they are the
-- rates the billing path actually charges. Nothing downstream changes. What is new is where
-- those numbers come from -- either a person typed them, as today, or they are derived as
-- list price x discount and maintained by the sync job.
--
-- The discount stays a human decision. Nothing derives it, because it comes from a commercial
-- agreement that is not published anywhere a machine can read.
--
-- Why side tables instead of columns on managed_model and model_runtime, which would read
-- more naturally: the registry query selects `model.*` and `runtime.*`, and the rows land in
-- Pydantic models declared with extra="forbid". A column added to either table therefore
-- becomes a field the *previous* release cannot parse -- deploy this, then roll the code back,
-- and the registry answers 500 until someone drops the columns by hand on a live database.
-- A side table keeps `model.*` the shape it has always been, so a rollback is just the old
-- package again. managed_model_metadata and model_provider_metadata in 001 exist for the same
-- reason; this follows them.

CREATE TABLE public.model_runtime_price (
    runtime_id UUID PRIMARY KEY
        REFERENCES public.model_runtime (id) ON DELETE CASCADE,
    -- Percent of list price charged for models on this connection. 90 means a 10% discount.
    -- NULL charges list price. A model may override it.
    price_discount_percent NUMERIC(6,3),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT model_runtime_price_discount_range
        CHECK (price_discount_percent IS NULL
               OR (price_discount_percent > 0 AND price_discount_percent <= 100))
);

CREATE TABLE public.managed_model_price (
    model_id UUID PRIMARY KEY
        REFERENCES public.managed_model (id) ON DELETE CASCADE,
    -- 'manual' keeps today's behaviour exactly: the sync job skips the row entirely. A model
    -- with no row here is 'manual' too, which is why every model that exists today needs no
    -- backfill.
    price_source TEXT NOT NULL DEFAULT 'manual',
    -- Which catalog entry this model is priced from. Set by a person once, then the sync job
    -- follows it. Matching on the model name instead would be wrong in a way nobody sees: the
    -- bill would still look plausible.
    price_reference TEXT,
    list_input_cost_per_million NUMERIC(18,8),
    list_output_cost_per_million NUMERIC(18,8),
    list_cached_cost_per_million NUMERIC(18,8),
    list_cache_write_cost_per_million NUMERIC(18,8),
    -- Overrides the connection discount for this one model. NULL inherits it.
    price_discount_percent NUMERIC(6,3),
    price_synced_at TIMESTAMPTZ,
    -- ok, unmapped when the reference resolves to nothing, stale when the source could not be
    -- read and the previous rates were kept, review_needed when the new list price moved far
    -- enough that a person should look before it reaches a bill.
    price_sync_status TEXT,
    price_sync_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT managed_model_price_source_check
        CHECK (price_source = ANY (ARRAY['manual'::text, 'azure_retail'::text, 'anthropic'::text])),
    CONSTRAINT managed_model_price_reference_required
        CHECK (price_source = 'manual' OR price_reference IS NOT NULL),
    CONSTRAINT managed_model_price_discount_range
        CHECK (price_discount_percent IS NULL
               OR (price_discount_percent > 0 AND price_discount_percent <= 100)),
    CONSTRAINT managed_model_price_sync_status_check
        CHECK (price_sync_status IS NULL OR price_sync_status = ANY (
            ARRAY['ok'::text, 'unmapped'::text, 'stale'::text, 'review_needed'::text])),
    CONSTRAINT managed_model_price_list_input_check
        CHECK (list_input_cost_per_million IS NULL OR list_input_cost_per_million >= 0),
    CONSTRAINT managed_model_price_list_output_check
        CHECK (list_output_cost_per_million IS NULL OR list_output_cost_per_million >= 0),
    CONSTRAINT managed_model_price_list_cached_check
        CHECK (list_cached_cost_per_million IS NULL OR list_cached_cost_per_million >= 0),
    CONSTRAINT managed_model_price_list_cache_write_check
        CHECK (list_cache_write_cost_per_million IS NULL OR list_cache_write_cost_per_million >= 0)
);

COMMENT ON TABLE public.model_runtime_price IS
    'Discount that applies to every model on a connection. Absent row means list price.';
COMMENT ON TABLE public.managed_model_price IS
    'Where one model''s charged rates come from. Absent row means a person typed them.';

-- Every model already in the registry was priced by hand, so an absent row is the honest
-- default and the backfill is a no-op. Stated explicitly because a future reader will wonder.
