-- Official list price + discount.
--
-- The four existing managed_model.*_cost_per_million columns keep their meaning: they are the
-- rates the billing path actually charges. Nothing downstream changes. What is new is where
-- those numbers come from -- either a person typed them, as today, or they are derived as
-- list price x discount and maintained by the sync job.
--
-- The discount stays a human decision. Nothing derives it, because it comes from a commercial
-- agreement that is not published anywhere a machine can read.

ALTER TABLE model_runtime
    ADD COLUMN price_discount_percent NUMERIC(6,3),
    ADD CONSTRAINT model_runtime_price_discount_range
        CHECK (price_discount_percent IS NULL
               OR (price_discount_percent > 0 AND price_discount_percent <= 100));

COMMENT ON COLUMN model_runtime.price_discount_percent IS
    'Percent of list price charged for models on this connection. 90 means a 10% discount. NULL charges list price. A model may override it.';

ALTER TABLE managed_model
    -- 'manual' keeps today's behaviour exactly: the sync job skips the row entirely.
    ADD COLUMN price_source TEXT NOT NULL DEFAULT 'manual',
    -- Which catalog entry this model is priced from. Set by a person once, then the sync job
    -- follows it. Matching on the model name instead would be wrong in a way nobody sees: the
    -- bill would still look plausible.
    ADD COLUMN price_reference TEXT,
    ADD COLUMN list_input_cost_per_million NUMERIC(18,8),
    ADD COLUMN list_output_cost_per_million NUMERIC(18,8),
    ADD COLUMN list_cached_cost_per_million NUMERIC(18,8),
    ADD COLUMN list_cache_write_cost_per_million NUMERIC(18,8),
    ADD COLUMN price_discount_percent NUMERIC(6,3),
    ADD COLUMN price_synced_at TIMESTAMPTZ,
    ADD COLUMN price_sync_status TEXT,
    ADD COLUMN price_sync_message TEXT,
    ADD CONSTRAINT managed_model_price_source_check
        CHECK (price_source = ANY (ARRAY['manual'::text, 'azure_retail'::text, 'anthropic'::text])),
    ADD CONSTRAINT managed_model_price_reference_required
        CHECK (price_source = 'manual' OR price_reference IS NOT NULL),
    ADD CONSTRAINT managed_model_price_discount_range
        CHECK (price_discount_percent IS NULL
               OR (price_discount_percent > 0 AND price_discount_percent <= 100)),
    ADD CONSTRAINT managed_model_price_sync_status_check
        CHECK (price_sync_status IS NULL OR price_sync_status = ANY (
            ARRAY['ok'::text, 'unmapped'::text, 'stale'::text, 'review_needed'::text])),
    ADD CONSTRAINT managed_model_list_input_cost_check
        CHECK (list_input_cost_per_million IS NULL OR list_input_cost_per_million >= 0),
    ADD CONSTRAINT managed_model_list_output_cost_check
        CHECK (list_output_cost_per_million IS NULL OR list_output_cost_per_million >= 0),
    ADD CONSTRAINT managed_model_list_cached_cost_check
        CHECK (list_cached_cost_per_million IS NULL OR list_cached_cost_per_million >= 0),
    ADD CONSTRAINT managed_model_list_cache_write_cost_check
        CHECK (list_cache_write_cost_per_million IS NULL OR list_cache_write_cost_per_million >= 0);

COMMENT ON COLUMN managed_model.price_source IS
    'manual keeps the typed rates untouched. azure_retail and anthropic derive them as list price x discount.';
COMMENT ON COLUMN managed_model.price_reference IS
    'Catalog entry this model is priced from, confirmed by a person. Never inferred at sync time.';
COMMENT ON COLUMN managed_model.price_discount_percent IS
    'Overrides the connection discount for this one model. NULL inherits it.';
COMMENT ON COLUMN managed_model.price_sync_status IS
    'ok, unmapped when the reference resolves to nothing, stale when the source could not be read and the previous rates were kept, review_needed when the new list price moved far enough that a person should look before it reaches a bill.';

-- Every model already in the registry was priced by hand, so 'manual' is the honest default and
-- the backfill is a no-op. Stated explicitly because a future reader will wonder.
