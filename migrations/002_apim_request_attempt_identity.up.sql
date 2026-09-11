DROP INDEX public.token_usage_request_id_idx;

CREATE INDEX token_usage_request_id_idx
    ON public.token_usage (request_id, ts DESC);

COMMENT ON COLUMN public.token_usage.request_id IS
    'Caller request identifier; retries may share this value.';
COMMENT ON COLUMN public.token_usage.correlation_id IS
    'APIM attempt identifier used for reconciliation and exact request detail lookup.';