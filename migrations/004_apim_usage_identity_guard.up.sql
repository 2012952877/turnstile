CREATE TABLE apim_usage_identity (
    correlation_id TEXT PRIMARY KEY CHECK (length(btrim(correlation_id)) BETWEEN 1 AND 255),
    usage_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    first_received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE apim_usage_discrepancy (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usage_id TEXT NOT NULL REFERENCES token_usage(id) ON DELETE RESTRICT,
    input_tokens BIGINT NOT NULL,
    cached_tokens BIGINT NOT NULL,
    cache_write_tokens BIGINT NOT NULL,
    output_tokens BIGINT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (usage_id, input_tokens, cached_tokens, cache_write_tokens, output_tokens)
);

CREATE FUNCTION guard_apim_usage_identity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    candidates TEXT[];
    stored token_usage%ROWTYPE;
    identity apim_usage_identity%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (OLD.usage_domain = 'apim' OR NEW.usage_domain = 'apim') AND
           (NEW.id, NEW.correlation_id, NEW.usage_domain, NEW.user_id)
           IS DISTINCT FROM (OLD.id, OLD.correlation_id, OLD.usage_domain, OLD.user_id) THEN
            RAISE EXCEPTION 'APIM attempt identity is immutable';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.usage_domain <> 'apim' THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('apim-attempt:' || NEW.correlation_id, 0));
    SELECT array_agg(candidate.id) INTO candidates FROM (
        SELECT id FROM token_usage
        WHERE (usage_domain = 'apim' AND correlation_id = NEW.correlation_id) OR id = NEW.id
        ORDER BY id LIMIT 2
    ) candidate;
    IF cardinality(candidates) > 1 THEN
        RAISE EXCEPTION 'APIM correlation identity is ambiguous';
    END IF;
    IF cardinality(candidates) = 1 THEN
        SELECT * INTO STRICT stored FROM token_usage WHERE id = candidates[1];
        IF (stored.correlation_id, stored.usage_domain, stored.user_id)
           IS DISTINCT FROM (NEW.correlation_id, NEW.usage_domain, NEW.user_id) THEN
            RAISE EXCEPTION 'APIM correlation identity conflicts with stored usage';
        END IF;
        NEW.id := stored.id;
    END IF;
    INSERT INTO apim_usage_identity (correlation_id, usage_id, user_id)
        VALUES (NEW.correlation_id, NEW.id, NEW.user_id)
        ON CONFLICT (correlation_id) DO UPDATE SET correlation_id = EXCLUDED.correlation_id
        RETURNING * INTO identity;
    IF identity.user_id IS DISTINCT FROM NEW.user_id THEN
        RAISE EXCEPTION 'APIM correlation belongs to another person';
    END IF;
    NEW.id := identity.usage_id;
    IF stored.id IS NOT NULL AND NOT stored.estimated AND NOT NEW.estimated
       AND (stored.input_tokens, stored.cached_tokens, stored.cache_write_tokens, stored.output_tokens)
           IS DISTINCT FROM (NEW.input_tokens, NEW.cached_tokens, NEW.cache_write_tokens, NEW.output_tokens) THEN
        INSERT INTO apim_usage_discrepancy (
            usage_id, input_tokens, cached_tokens, cache_write_tokens, output_tokens
        ) VALUES (stored.id, NEW.input_tokens, NEW.cached_tokens, NEW.cache_write_tokens, NEW.output_tokens)
            ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER token_usage_apim_identity
BEFORE INSERT OR UPDATE ON token_usage
FOR EACH ROW EXECUTE FUNCTION guard_apim_usage_identity();

CREATE TRIGGER apim_usage_discrepancy_immutable
BEFORE UPDATE OR DELETE ON apim_usage_discrepancy
FOR EACH ROW EXECUTE FUNCTION reject_budget_reservation_finalization_mutation();