CREATE TABLE billable_request_attempt (
    id UUID PRIMARY KEY,
    operation_key TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL CHECK (plan_sha256 ~ '^[a-f0-9]{64}$'),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('person', 'application', 'system')),
    scope_id TEXT NOT NULL,
    period_start DATE NOT NULL CHECK (extract(day FROM period_start) = 1),
    model_id TEXT NOT NULL,
    model_key TEXT NOT NULL,
    reserved_tokens BIGINT NOT NULL CHECK (reserved_tokens > 0),
    authorization_id UUID,
    attempt_index INTEGER NOT NULL CHECK (attempt_index > 0),
    state TEXT NOT NULL CHECK (state IN ('started', 'exact', 'uncertain')),
    actual_tokens BIGINT CHECK (actual_tokens >= 0),
    correlation_id TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (operation_key, attempt_index),
    UNIQUE NULLS NOT DISTINCT (operation_key, authorization_id),
    CHECK ((state = 'exact') = (actual_tokens IS NOT NULL))
);

CREATE INDEX billable_request_budget_scope
ON billable_request_attempt (scope_type, scope_id, period_start);

CREATE VIEW billable_request_effective_v1 AS
SELECT attempt.*, canonical.total_tokens AS canonical_tokens,
       CASE WHEN COALESCE(canonical.total_tokens, attempt.actual_tokens, recovery.total_tokens)
                      IS NOT NULL THEN 'exact' ELSE attempt.state END AS effective_state,
       COALESCE(canonical.total_tokens, attempt.actual_tokens, recovery.total_tokens) AS effective_tokens
FROM billable_request_attempt attempt
LEFT JOIN LATERAL (
    SELECT SUM(usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT AS total_tokens
    FROM token_usage usage
    LEFT JOIN token_usage_application_attribution attribution ON attribution.usage_id = usage.id
    WHERE usage.request_id = attempt.id::text AND usage.correlation_id = attempt.correlation_id
      AND usage.usage_domain = 'apim' AND (NOT usage.estimated OR usage.status_code >= 400)
      AND usage.model_id = attempt.model_id
      AND ((attempt.scope_type = 'person' AND usage.user_id = attempt.scope_id)
        OR (attempt.scope_type = 'application' AND attribution.application_id::text = attempt.scope_id)
        OR (attempt.scope_type = 'system' AND usage.request_source = 'gateway-publication-probe'))
) canonical ON TRUE
LEFT JOIN budget_reservation_finalization_effective recovery
  ON recovery.correlation_id = attempt.id::text AND recovery.scope_type = attempt.scope_type
 AND recovery.scope_id = attempt.scope_id AND recovery.period_start = attempt.period_start
 AND recovery.finalization_kind <> 'unverified_upper_bound';

CREATE VIEW billable_request_effective AS SELECT * FROM billable_request_effective_v1;

CREATE VIEW budget_ordinary_usage_v1 AS
SELECT usage.* FROM budget_scope_usage usage
WHERE NOT EXISTS (
    SELECT 1 FROM billable_request_attempt attempt
    WHERE attempt.scope_type = usage.scope_type AND attempt.scope_id = usage.scope_id
      AND (attempt.id::text = usage.correlation_id OR EXISTS (
          SELECT 1 FROM token_usage canonical
          LEFT JOIN token_usage_application_attribution attribution ON attribution.usage_id = canonical.id
          WHERE canonical.request_id = attempt.id::text
            AND canonical.correlation_id = attempt.correlation_id
            AND canonical.correlation_id = usage.correlation_id
            AND canonical.model_id = attempt.model_id AND canonical.usage_domain = 'apim'
            AND ((attempt.scope_type = 'person' AND canonical.user_id = attempt.scope_id)
              OR (attempt.scope_type = 'application' AND attribution.application_id::text = attempt.scope_id))
      ))
);

CREATE FUNCTION budget_scope_pending_request_tokens(
    requested_scope_type TEXT, requested_scope_id TEXT, requested_period_start DATE
) RETURNS BIGINT LANGUAGE sql STABLE AS $$
SELECT COALESCE(SUM(reserved_tokens), 0)::BIGINT FROM billable_request_effective
WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
  AND period_start = requested_period_start AND effective_state <> 'exact';
$$;

CREATE OR REPLACE FUNCTION budget_scope_confirmed_tokens(
    requested_scope_type TEXT, requested_scope_id TEXT,
    requested_period_start DATE, requested_period_end DATE
) RETURNS BIGINT LANGUAGE sql STABLE AS $$
SELECT COALESCE((SELECT SUM(total_tokens) FROM budget_ordinary_usage_v1
    WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
      AND occurred_at >= requested_period_start::timestamp AT TIME ZONE 'UTC'
      AND occurred_at < requested_period_end::timestamp AT TIME ZONE 'UTC'), 0)::BIGINT
  + COALESCE((SELECT SUM(effective_tokens) FROM billable_request_effective
    WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
      AND period_start >= requested_period_start AND period_start < requested_period_end
      AND effective_state = 'exact'), 0)::BIGINT;
$$;

CREATE FUNCTION guard_billable_request_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Billable request attempts cannot be deleted';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['state', 'actual_tokens', 'correlation_id', 'evidence', 'updated_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['state', 'actual_tokens', 'correlation_id', 'evidence', 'updated_at'])
       OR OLD.state = 'exact' THEN
        RAISE EXCEPTION 'Billable request identity and exact acknowledgements are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER billable_request_attempt_guard BEFORE UPDATE OR DELETE ON billable_request_attempt
FOR EACH ROW EXECUTE FUNCTION guard_billable_request_attempt();