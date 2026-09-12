CREATE TABLE budget_evidence_policy (
    version SMALLINT PRIMARY KEY CHECK (version = 2),
    effective_at TIMESTAMPTZ
);
INSERT INTO budget_evidence_policy (version, effective_at) VALUES (2, NULL);

CREATE FUNCTION guard_budget_evidence_policy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' OR OLD.effective_at IS NOT NULL OR NEW.version <> OLD.version
       OR NEW.effective_at IS NULL OR NEW.effective_at < clock_timestamp() THEN
        RAISE EXCEPTION 'Budget evidence cutover must be scheduled once, never retroactively';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER budget_evidence_policy_guard
BEFORE INSERT OR UPDATE OR DELETE ON budget_evidence_policy
FOR EACH ROW EXECUTE FUNCTION guard_budget_evidence_policy();

CREATE FUNCTION strict_budget_evidence(admitted_at TIMESTAMPTZ) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
SELECT COALESCE(CURRENT_TIMESTAMP >= effective_at AND admitted_at >= effective_at, FALSE)
FROM budget_evidence_policy WHERE version = 2;
$$;

CREATE INDEX billable_request_gateway_correlation
ON billable_request_attempt (scope_type, scope_id, correlation_id) WHERE correlation_id IS NOT NULL;

CREATE FUNCTION budget_bound_attempt(
    requested_scope_type TEXT, requested_scope_id TEXT, request_key TEXT
) RETURNS UUID LANGUAGE plpgsql STABLE AS $$
DECLARE candidates UUID[];
BEGIN
    SELECT array_agg(candidate.id) INTO candidates FROM (
        SELECT id FROM billable_request_attempt
        WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
          AND (id::text = request_key OR (correlation_id = request_key AND strict_budget_evidence(created_at)))
        LIMIT 2
    ) candidate;
    IF cardinality(candidates) > 1 THEN
        RAISE EXCEPTION 'Budget reservation attempt binding is ambiguous';
    END IF;
    RETURN candidates[1];
END;
$$;

CREATE TABLE budget_reservation_admission (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('person', 'application')),
    scope_id TEXT NOT NULL CHECK (length(btrim(scope_id)) BETWEEN 1 AND 255),
    correlation_id TEXT NOT NULL CHECK (length(btrim(correlation_id)) BETWEEN 1 AND 255),
    created_at TIMESTAMPTZ NOT NULL,
    reserved_tokens BIGINT NOT NULL CHECK (reserved_tokens >= 0),
    PRIMARY KEY (scope_type, scope_id, correlation_id)
);
CREATE TRIGGER budget_reservation_admission_immutable
BEFORE UPDATE OR DELETE ON budget_reservation_admission
FOR EACH ROW EXECUTE FUNCTION reject_budget_reservation_finalization_mutation();

CREATE VIEW budget_usage_evidence AS
SELECT scope.scope_type, scope.scope_id, usage.id AS usage_id, usage.correlation_id,
       COALESCE(attempt.id::text, usage.correlation_id) AS request_key,
       attempt.id AS attempt_id,
       COALESCE(attempt.created_at, admission.created_at, recovery.reservation_created_at, usage.ts) AS admitted_at,
       COALESCE(attempt.period_start, date_trunc('month', COALESCE(
           admission.created_at, recovery.reservation_created_at, usage.ts
       ) AT TIME ZONE 'UTC')::date) AS period_start,
       usage.ts, usage.organization_id, usage.department_id,
       (usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT AS total_tokens,
       CASE WHEN usage.reconciled_at IS NOT NULL AND usage.ingest_error = 'stream_cache_usage_unavailable'
         THEN usage.input_tokens + usage.output_tokens
         ELSE usage.input_tokens + usage.cached_tokens + usage.output_tokens
       END::BIGINT AS measured_total_tokens,
       CASE WHEN usage.input_tokens >= 0 AND usage.cached_tokens >= 0 AND usage.output_tokens >= 0
                  AND usage.cache_write_tokens BETWEEN 0 AND usage.cached_tokens
                  AND length(btrim(usage.correlation_id)) > 0
            THEN CASE WHEN NOT usage.estimated AND usage.ingest_error IS NULL THEN 3
                      WHEN usage.reconciled_at IS NOT NULL
                           AND usage.ingest_error = 'stream_cache_usage_unavailable' THEN 1 END
       END AS evidence_rank,
       COALESCE(usage.reconciled_at, identity.first_received_at, usage.ts) AS received_at
FROM token_usage usage
LEFT JOIN token_usage_application_attribution attribution ON attribution.usage_id = usage.id
LEFT JOIN billable_request_attempt system_attempt ON system_attempt.id::text = usage.request_id
 AND system_attempt.scope_type = 'system' AND system_attempt.correlation_id = usage.correlation_id
 AND system_attempt.model_id = usage.model_id AND usage.request_source = 'gateway-publication-probe'
CROSS JOIN LATERAL (VALUES ('person'::text, usage.user_id),
    ('application'::text, attribution.application_id::text), ('system'::text, system_attempt.scope_id)
) scope(scope_type, scope_id)
LEFT JOIN billable_request_attempt attempt ON attempt.id::text = usage.request_id
 AND attempt.scope_type = scope.scope_type AND attempt.scope_id = scope.scope_id
 AND attempt.correlation_id = usage.correlation_id AND attempt.model_id = usage.model_id
LEFT JOIN budget_reservation_admission admission ON admission.scope_type = scope.scope_type
 AND admission.scope_id = scope.scope_id
 AND admission.correlation_id = COALESCE(attempt.id::text, usage.correlation_id)
LEFT JOIN budget_reservation_finalization_effective recovery ON recovery.scope_type = scope.scope_type
 AND recovery.scope_id = scope.scope_id
 AND recovery.correlation_id = COALESCE(attempt.id::text, usage.correlation_id)
LEFT JOIN apim_usage_identity identity ON identity.correlation_id = usage.correlation_id
WHERE usage.usage_domain = 'apim' AND scope.scope_id IS NOT NULL;

CREATE VIEW budget_evidence_candidates AS
SELECT scope_type, scope_id, request_key, admitted_at, period_start,
       measured_total_tokens AS total_tokens, evidence_rank, received_at,
       0::BIGINT AS evidence_order, 'usage:' || usage_id AS evidence_key,
       usage_id, organization_id, department_id, correlation_id
FROM budget_usage_evidence WHERE evidence_rank IS NOT NULL AND strict_budget_evidence(admitted_at)
UNION ALL
SELECT scope_type, scope_id, id::text, created_at, period_start, actual_tokens,
       2, updated_at, 0::BIGINT, 'ack:' || id::text, NULL, 'unattributed', 'unattributed', id::text
FROM billable_request_attempt WHERE state = 'exact' AND strict_budget_evidence(created_at)
UNION ALL
SELECT evidence.scope_type, evidence.scope_id, COALESCE(attempt.id::text, evidence.correlation_id),
       COALESCE(attempt.created_at, admission.created_at, evidence.reservation_created_at),
       COALESCE(attempt.period_start, date_trunc('month', COALESCE(
           admission.created_at, evidence.reservation_created_at
       ) AT TIME ZONE 'UTC')::date),
       evidence.total_tokens, 1, evidence.finalized_at, evidence.id, 'recovery:' || evidence.id::text,
       NULL, 'unattributed', 'unattributed', evidence.correlation_id
FROM budget_reservation_finalization evidence
LEFT JOIN billable_request_attempt attempt ON attempt.id = budget_bound_attempt(
    evidence.scope_type, evidence.scope_id, evidence.correlation_id
)
LEFT JOIN budget_reservation_admission admission ON admission.scope_type = evidence.scope_type
 AND admission.scope_id = evidence.scope_id AND admission.correlation_id = evidence.correlation_id
WHERE evidence.finalization_kind IN ('exact_usage', 'terminal_zero')
  AND evidence.input_tokens IS NOT NULL AND evidence.input_tokens >= 0
  AND evidence.output_tokens IS NOT NULL AND evidence.output_tokens >= 0
  AND evidence.total_tokens = evidence.input_tokens + evidence.output_tokens
  AND strict_budget_evidence(COALESCE(attempt.created_at, admission.created_at, evidence.reservation_created_at));

CREATE VIEW budget_evidence_selected AS
SELECT DISTINCT ON (scope_type, scope_id, request_key) * FROM budget_evidence_candidates
ORDER BY scope_type, scope_id, request_key, evidence_rank DESC, received_at, evidence_order, evidence_key;

CREATE VIEW budget_evidence_conflicts AS
SELECT chosen.scope_type, chosen.scope_id, chosen.request_key, chosen.period_start,
       chosen.evidence_key AS selected_evidence, chosen.total_tokens AS selected_tokens,
       alternative.evidence_key AS conflicting_evidence, alternative.total_tokens AS conflicting_tokens
FROM budget_evidence_selected chosen
JOIN budget_evidence_candidates alternative USING (scope_type, scope_id, request_key)
WHERE chosen.total_tokens <> alternative.total_tokens;

CREATE OR REPLACE VIEW billable_request_effective AS
SELECT * FROM billable_request_effective_v1 WHERE NOT strict_budget_evidence(created_at)
UNION ALL
SELECT attempt.*, CASE WHEN chosen.evidence_rank = 3 THEN chosen.total_tokens END AS canonical_tokens,
       CASE WHEN chosen.total_tokens IS NOT NULL THEN 'exact' ELSE attempt.state END AS effective_state,
       chosen.total_tokens AS effective_tokens
FROM billable_request_attempt attempt
LEFT JOIN budget_evidence_selected chosen ON chosen.scope_type = attempt.scope_type
 AND chosen.scope_id = attempt.scope_id AND chosen.request_key = attempt.id::text
WHERE strict_budget_evidence(attempt.created_at);

CREATE VIEW budget_legacy_usage AS
SELECT usage.* FROM budget_ordinary_usage_v1 usage
LEFT JOIN budget_reservation_admission admission ON admission.scope_type = usage.scope_type
 AND admission.scope_id = usage.scope_id AND admission.correlation_id = usage.correlation_id
LEFT JOIN budget_reservation_finalization_effective recovery ON recovery.scope_type = usage.scope_type
 AND recovery.scope_id = usage.scope_id AND recovery.correlation_id = usage.correlation_id
WHERE NOT strict_budget_evidence(COALESCE(admission.created_at, recovery.reservation_created_at, usage.occurred_at))
  AND NOT EXISTS (
    SELECT 1 FROM budget_usage_evidence evidence
    WHERE evidence.scope_type = usage.scope_type AND evidence.scope_id = usage.scope_id
      AND evidence.correlation_id = usage.correlation_id AND strict_budget_evidence(evidence.admitted_at)
  );

CREATE OR REPLACE FUNCTION budget_scope_confirmed_tokens(
    requested_scope_type TEXT, requested_scope_id TEXT,
    requested_period_start DATE, requested_period_end DATE
) RETURNS BIGINT LANGUAGE sql STABLE AS $$
SELECT COALESCE((SELECT SUM(total_tokens) FROM budget_legacy_usage
    WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
      AND occurred_at >= requested_period_start::timestamp AT TIME ZONE 'UTC'
      AND occurred_at < requested_period_end::timestamp AT TIME ZONE 'UTC'), 0)::BIGINT
  + COALESCE((SELECT SUM(effective_tokens) FROM billable_request_effective_v1
    WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
      AND period_start >= requested_period_start AND period_start < requested_period_end
      AND effective_state = 'exact' AND NOT strict_budget_evidence(created_at)), 0)::BIGINT
  + COALESCE((SELECT SUM(total_tokens) FROM budget_evidence_selected
    WHERE scope_type = requested_scope_type AND scope_id = requested_scope_id
      AND period_start >= requested_period_start AND period_start < requested_period_end), 0)::BIGINT;
$$;

CREATE FUNCTION settled_budget_reservations(
    requested_scope_type TEXT, requested_scope_id TEXT, wanted TEXT[]
) RETURNS TABLE(correlation_id TEXT) LANGUAGE sql STABLE AS $$
SELECT DISTINCT evidence.correlation_id FROM budget_usage_evidence evidence
JOIN token_usage usage ON usage.id = evidence.usage_id
WHERE evidence.scope_type = requested_scope_type AND evidence.scope_id = requested_scope_id
  AND evidence.correlation_id = ANY(wanted) AND NOT strict_budget_evidence(evidence.admitted_at)
  AND (NOT usage.estimated OR usage.status_code >= 400
       OR (usage.reconciled_at IS NOT NULL AND usage.ingest_error = 'stream_cache_usage_unavailable'))
UNION
SELECT evidence.correlation_id FROM budget_reservation_finalization_effective evidence
WHERE evidence.scope_type = requested_scope_type AND evidence.scope_id = requested_scope_id
  AND evidence.correlation_id = ANY(wanted) AND evidence.finalization_kind <> 'unverified_upper_bound'
  AND NOT strict_budget_evidence(evidence.reservation_created_at)
UNION
SELECT chosen.request_key FROM budget_evidence_selected chosen
WHERE chosen.scope_type = requested_scope_type AND chosen.scope_id = requested_scope_id
  AND chosen.request_key = ANY(wanted)
UNION
SELECT candidate.correlation_id FROM budget_evidence_candidates candidate
JOIN budget_evidence_selected chosen USING (scope_type, scope_id, request_key)
WHERE candidate.scope_type = requested_scope_type AND candidate.scope_id = requested_scope_id
  AND candidate.correlation_id = ANY(wanted);
$$;