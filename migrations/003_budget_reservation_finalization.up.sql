CREATE TABLE budget_reservation_finalization (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('person', 'application')),
    scope_id TEXT NOT NULL CHECK (length(btrim(scope_id)) BETWEEN 1 AND 255),
    period_start DATE NOT NULL CHECK (extract(day FROM period_start) = 1),
    correlation_id TEXT NOT NULL CHECK (length(btrim(correlation_id)) BETWEEN 1 AND 255),
    reservation_created_at TIMESTAMPTZ NOT NULL,
    reservation_tokens BIGINT NOT NULL CHECK (reservation_tokens >= 0),
    evidence_at TIMESTAMPTZ NOT NULL CHECK (evidence_at >= reservation_created_at),
    status_code INTEGER CHECK (status_code BETWEEN 0 AND 599),
    input_tokens BIGINT CHECK (input_tokens >= 0),
    output_tokens BIGINT CHECK (output_tokens >= 0),
    total_tokens BIGINT NOT NULL CHECK (total_tokens >= 0),
    finalization_kind TEXT NOT NULL CHECK (
        finalization_kind IN ('exact_usage', 'terminal_zero', 'unverified_upper_bound')
    ),
    source TEXT NOT NULL CHECK (
        source IN ('apim_gateway_llm_log', 'apim_gateway_log', 'reservation_timeout')
    ),
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scope_type, scope_id, correlation_id, finalization_kind, source, evidence_at),
    CHECK (
        (finalization_kind = 'exact_usage' AND source = 'apim_gateway_llm_log'
            AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL
            AND total_tokens = input_tokens + output_tokens)
        OR (finalization_kind = 'terminal_zero' AND source = 'apim_gateway_log'
            AND input_tokens IS NOT NULL AND input_tokens = 0
            AND output_tokens IS NOT NULL AND output_tokens = 0 AND total_tokens = 0
            AND status_code IS NOT NULL AND status_code >= 400)
        OR (finalization_kind = 'unverified_upper_bound' AND source = 'reservation_timeout'
            AND input_tokens IS NULL AND output_tokens IS NULL AND status_code IS NULL
            AND total_tokens = reservation_tokens)
    )
);

CREATE INDEX budget_reservation_finalization_scope_idx
    ON budget_reservation_finalization (scope_type, scope_id, period_start, correlation_id);

CREATE FUNCTION reject_budget_reservation_finalization_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Budget reservation evidence is append-only';
END;
$$;

CREATE TRIGGER budget_reservation_finalization_immutable
    BEFORE UPDATE OR DELETE ON budget_reservation_finalization
    FOR EACH ROW EXECUTE FUNCTION reject_budget_reservation_finalization_mutation();

CREATE VIEW budget_reservation_finalization_effective AS
SELECT DISTINCT ON (scope_type, scope_id, correlation_id)
    scope_type, scope_id, period_start, correlation_id, reservation_created_at,
    reservation_tokens, evidence_at, status_code, input_tokens, output_tokens,
    total_tokens, finalization_kind, source
FROM budget_reservation_finalization
ORDER BY scope_type, scope_id, correlation_id,
    CASE finalization_kind WHEN 'exact_usage' THEN 3 WHEN 'terminal_zero' THEN 2 ELSE 1 END DESC,
    evidence_at DESC, id DESC;

CREATE VIEW budget_reservation_recovery AS
SELECT evidence.* FROM budget_reservation_finalization_effective evidence
WHERE evidence.finalization_kind <> 'unverified_upper_bound'
AND NOT EXISTS (
    SELECT 1 FROM token_usage usage
    LEFT JOIN token_usage_application_attribution attribution ON attribution.usage_id = usage.id
    WHERE usage.usage_domain = 'apim' AND usage.correlation_id = evidence.correlation_id
    AND (NOT usage.estimated OR usage.status_code >= 400
        OR (usage.reconciled_at IS NOT NULL AND usage.ingest_error = 'stream_cache_usage_unavailable'))
    AND ((evidence.scope_type = 'person' AND usage.user_id = evidence.scope_id)
        OR (evidence.scope_type = 'application'
            AND attribution.application_id::TEXT = evidence.scope_id))
);

CREATE VIEW budget_scope_usage AS
SELECT scope.scope_type, scope.scope_id, usage.correlation_id, usage.ts AS occurred_at,
    usage.input_tokens, usage.cached_tokens, usage.cache_write_tokens, usage.output_tokens,
    (usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT AS total_tokens,
    usage.status_code, usage.estimated_cost, FALSE AS recovered, usage.et, usage.latency_ms
FROM token_usage usage
LEFT JOIN token_usage_application_attribution attribution ON attribution.usage_id = usage.id
CROSS JOIN LATERAL (
    VALUES ('person'::TEXT, usage.user_id), ('application'::TEXT, attribution.application_id::TEXT)
) scope(scope_type, scope_id)
WHERE usage.usage_domain = 'apim' AND scope.scope_id IS NOT NULL
AND NOT EXISTS (
    SELECT 1 FROM budget_reservation_recovery recovery
    WHERE recovery.scope_type = scope.scope_type AND recovery.scope_id = scope.scope_id
        AND recovery.correlation_id = usage.correlation_id
)
UNION ALL
SELECT recovery.scope_type, recovery.scope_id, recovery.correlation_id,
    recovery.reservation_created_at, recovery.input_tokens, 0::BIGINT, 0::BIGINT,
    recovery.output_tokens, recovery.total_tokens, COALESCE(recovery.status_code, 0),
    0::NUMERIC, TRUE, 0::DOUBLE PRECISION, NULL::INTEGER
FROM budget_reservation_recovery recovery;

CREATE FUNCTION budget_scope_confirmed_tokens(
    requested_scope_type TEXT, requested_scope_id TEXT,
    requested_period_start DATE, requested_period_end DATE
) RETURNS BIGINT LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(total_tokens), 0)::BIGINT FROM budget_scope_usage
    WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
        AND occurred_at >= requested_period_start::TIMESTAMP AT TIME ZONE 'UTC'
        AND occurred_at < requested_period_end::TIMESTAMP AT TIME ZONE 'UTC';
$$;

CREATE TABLE gateway_application_ledger_state (
    period_start DATE NOT NULL CHECK (extract(day FROM period_start) = 1),
    application_id UUID NOT NULL REFERENCES gateway_application(id) ON DELETE RESTRICT,
    token_limit BIGINT NOT NULL CHECK (token_limit > 0),
    confirmed_tokens BIGINT NOT NULL CHECK (confirmed_tokens >= 0),
    pending_reserved_tokens BIGINT NOT NULL CHECK (pending_reserved_tokens >= 0),
    pending_reservation_count INTEGER NOT NULL CHECK (pending_reservation_count >= 0),
    finalized_upper_bound_tokens BIGINT NOT NULL CHECK (finalized_upper_bound_tokens >= 0),
    finalized_upper_bound_count INTEGER NOT NULL CHECK (finalized_upper_bound_count >= 0),
    stale_reservation_count INTEGER NOT NULL CHECK (
        stale_reservation_count BETWEEN 0 AND pending_reservation_count
    ),
    oldest_reservation_at TIMESTAMPTZ,
    available_tokens BIGINT NOT NULL CHECK (
        available_tokens = GREATEST(
            token_limit - confirmed_tokens - pending_reserved_tokens - finalized_upper_bound_tokens, 0
        )
    ),
    snapshot_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (period_start, application_id),
    CHECK ((pending_reservation_count = 0 AND oldest_reservation_at IS NULL)
        OR (pending_reservation_count > 0 AND oldest_reservation_at IS NOT NULL))
);