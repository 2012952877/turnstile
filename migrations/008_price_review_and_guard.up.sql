-- Keep a price that is waiting for review out of the baseline it is reviewed against.
--
-- 007 stored one set of list_* columns and the sync wrote them on every run, including the runs
-- that refused to charge the new price. That made the refusal useless: the first run marked the
-- row review_needed and moved the baseline to the unapproved figure, so the second run compared
-- the figure against itself, found no drift, and charged it. A threshold nobody has to approve
-- is not a threshold.
--
-- So the two ideas get two homes. list_* stays what it has always been -- the price this model
-- is currently charged from, which only moves when a sync actually accepts it. pending_list_price
-- holds what the source is now publishing while a person decides. Stored as one document rather
-- than four more columns because it is one proposal, and it is read as a whole or not at all.

ALTER TABLE public.managed_model_price
    ADD COLUMN pending_list_price JSONB;

COMMENT ON COLUMN public.managed_model_price.pending_list_price IS
    'The list price the source now publishes, held back because it moved further than the review threshold. NULL once a sync accepts it into list_*.';

-- `superseded` is new: the sync planned against a price configuration that changed before the
-- write landed, so it wrote nothing. Distinct from `stale`, which means the source could not be
-- read -- the rates are equally untouched but the reason a person should act on is different.
ALTER TABLE public.managed_model_price
    DROP CONSTRAINT managed_model_price_sync_status_check;

ALTER TABLE public.managed_model_price
    ADD CONSTRAINT managed_model_price_sync_status_check
        CHECK (price_sync_status IS NULL OR price_sync_status = ANY (
            ARRAY['ok'::text, 'unmapped'::text, 'stale'::text,
                  'review_needed'::text, 'superseded'::text]));
