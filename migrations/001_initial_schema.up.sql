-- Turnstile initial schema for a brand-new PostgreSQL 16+ database.
-- Generated from the verified migration-040 baseline plus non-Router schema changes.
-- Contains no users, credentials, providers, runtimes, models, usage, or business data.

-- Turnstile database baseline after migrations 001-040.
-- Generated on PostgreSQL 16 from a clean database by the real migration runner.
-- Apply only to a brand-new empty PostgreSQL 16+ database.


--
-- PostgreSQL database dump
--


-- Dumped from database version 16.14 (Homebrew)
-- Dumped by pg_dump version 16.14 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: create_assistant_conversation_owner(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_assistant_conversation_owner() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO assistant_conversation_owner (conversation_id, created_by)
    VALUES (NEW.id, resolve_assistant_creator(NEW.owner_id));
    RETURN NEW;
END
$$;


--
-- Name: create_pinned_report_access(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_pinned_report_access() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO pinned_report_access (report_id, created_by, visibility)
    VALUES (
        NEW.id,
        resolve_assistant_creator(NEW.owner_id),
        CASE WHEN NEW.owner_id = 'system-finops-assistant' THEN 'public' ELSE 'private' END
    );
    RETURN NEW;
END
$$;


--
-- Name: resolve_assistant_creator(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.resolve_assistant_creator(legacy_owner text) RETURNS text
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE
        WHEN legacy_owner = 'system-finops-assistant' THEN COALESCE(
            (
                SELECT email
                FROM app_user
                WHERE role = 'owner' AND enabled
                ORDER BY created_at, email
                LIMIT 1
            ),
            legacy_owner
        )
        ELSE legacy_owner
    END
$$;


--
-- Name: token_observability_change(numeric, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_change(p_current numeric, p_previous numeric) RETURNS double precision
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT CASE
        WHEN p_previous = 0 THEN NULL
        ELSE ROUND(((p_current - p_previous) / p_previous) * 100, 2)::DOUBLE PRECISION
    END;
$$;


--
-- Name: token_observability_metric(timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_metric(p_from timestamp with time zone, p_to timestamp with time zone) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT jsonb_build_object(
        'et', COALESCE(SUM(et), 0)::DOUBLE PRECISION,
        'total_tokens', COALESCE(SUM(input_tokens + cached_tokens + output_tokens), 0)::BIGINT,
        'input_tokens', COALESCE(SUM(input_tokens), 0)::BIGINT,
        'cached_tokens', COALESCE(SUM(cached_tokens), 0)::BIGINT,
        'output_tokens', COALESCE(SUM(output_tokens), 0)::BIGINT,
        'calls', COUNT(*)::BIGINT
    )
    FROM token_usage
    WHERE ts >= p_from AND ts < p_to;
$$;


--
-- Name: token_observability_optimization(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_optimization(p_event_id uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_event optimization_event%ROWTYPE;
    v_before JSONB;
    v_after JSONB;
BEGIN
    SELECT * INTO v_event FROM optimization_event WHERE id = p_event_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    v_before := token_observability_workflow_metric(
        v_event.workflow,
        v_event.occurred_at - make_interval(days => v_event.comparison_window_days),
        v_event.occurred_at
    );
    v_after := token_observability_workflow_metric(
        v_event.workflow,
        v_event.occurred_at,
        v_event.occurred_at + make_interval(days => v_event.comparison_window_days)
    );

    RETURN jsonb_build_object(
        'id', v_event.id,
        'created_at', v_event.created_at,
        'workflow', v_event.workflow,
        'occurred_at', v_event.occurred_at,
        'label', v_event.label,
        'notes', v_event.notes,
        'comparison', jsonb_build_object(
            'window_days', v_event.comparison_window_days,
            'before', v_before,
            'after', v_after,
            'et_change_percent', token_observability_change(
                (v_after->>'et')::NUMERIC, (v_before->>'et')::NUMERIC
            ),
            'token_change_percent', token_observability_change(
                (v_after->>'total_tokens')::NUMERIC,
                (v_before->>'total_tokens')::NUMERIC
            )
        )
    );
END;
$$;


--
-- Name: token_observability_overview(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_overview(p_timezone text) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_now TIMESTAMPTZ := now();
    v_today_start TIMESTAMPTZ;
    v_month_start TIMESTAMPTZ;
    v_today JSONB;
    v_month JSONB;
    v_previous_today JSONB;
    v_previous_month JSONB;
    v_top JSONB;
    v_month_et NUMERIC;
    v_unattributed DOUBLE PRECISION;
    v_estimated DOUBLE PRECISION;
BEGIN
    v_today_start := date_trunc('day', v_now AT TIME ZONE p_timezone) AT TIME ZONE p_timezone;
    v_month_start := date_trunc('month', v_now AT TIME ZONE p_timezone) AT TIME ZONE p_timezone;
    v_today := token_observability_metric(v_today_start, v_now);
    v_month := token_observability_metric(v_month_start, v_now);
    v_previous_today := token_observability_metric(
        v_today_start - (v_now - v_today_start), v_today_start
    );
    v_previous_month := token_observability_metric(
        v_month_start - (v_now - v_month_start), v_month_start
    );
    v_month_et := (v_month->>'et')::NUMERIC;

    SELECT COALESCE(jsonb_agg(item ORDER BY et_sort DESC), '[]'::jsonb)
    INTO v_top
    FROM (
        SELECT jsonb_build_object(
            'workflow', workflow,
            'totals', jsonb_build_object(
                'et', SUM(et)::DOUBLE PRECISION,
                'total_tokens', SUM(input_tokens + cached_tokens + output_tokens)::BIGINT,
                'input_tokens', SUM(input_tokens)::BIGINT,
                'cached_tokens', SUM(cached_tokens)::BIGINT,
                'output_tokens', SUM(output_tokens)::BIGINT,
                'calls', COUNT(*)::BIGINT
            ),
            'share_percent', CASE WHEN v_month_et = 0 THEN 0
                ELSE ROUND((SUM(et) / v_month_et) * 100, 2)::DOUBLE PRECISION END,
            'et_sort', SUM(et)
        ) - 'et_sort' AS item,
        SUM(et) AS et_sort
        FROM token_usage
        WHERE ts >= v_month_start AND ts < v_now
        GROUP BY workflow
        ORDER BY SUM(et) DESC
        LIMIT 10
    ) ranked;

    SELECT
        COALESCE(100.0 * COUNT(*) FILTER (
            WHERE team = 'unattributed' OR user_ref = 'unattributed'
               OR agent = 'unattributed' OR workflow = 'unattributed'
        ) / NULLIF(COUNT(*), 0), 0),
        COALESCE(100.0 * COUNT(*) FILTER (WHERE estimated) / NULLIF(COUNT(*), 0), 0)
    INTO v_unattributed, v_estimated
    FROM token_usage
    WHERE ts >= v_month_start AND ts < v_now;

    RETURN jsonb_build_object(
        'as_of', v_now,
        'timezone', p_timezone,
        'today', jsonb_build_object(
            'from', v_today_start,
            'to', v_now,
            'totals', v_today,
            'comparison_percent', token_observability_change(
                (v_today->>'et')::NUMERIC, (v_previous_today->>'et')::NUMERIC
            )
        ),
        'month', jsonb_build_object(
            'from', v_month_start,
            'to', v_now,
            'totals', v_month,
            'comparison_percent', token_observability_change(
                (v_month->>'et')::NUMERIC, (v_previous_month->>'et')::NUMERIC
            )
        ),
        'top_workflows', v_top,
        'unattributed_percent', ROUND(v_unattributed::NUMERIC, 2)::DOUBLE PRECISION,
        'estimated_percent', ROUND(v_estimated::NUMERIC, 2)::DOUBLE PRECISION
    );
END;
$$;


--
-- Name: token_observability_run_detail(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_run_detail(p_run_id text) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
    WITH summary AS (
        SELECT
            run_id,
            MIN(ts) AS started_at,
            MAX(ts) AS ended_at,
            MIN(team) AS team,
            MIN(user_ref) AS user_ref,
            MIN(agent) AS agent,
            MIN(workflow) AS workflow,
            MIN(provider) AS provider,
            ARRAY_AGG(DISTINCT model ORDER BY model) AS models,
            COUNT(*)::INTEGER AS turn_count,
            jsonb_build_object(
                'et', SUM(et)::DOUBLE PRECISION,
                'total_tokens', SUM(input_tokens + cached_tokens + output_tokens)::BIGINT,
                'input_tokens', SUM(input_tokens)::BIGINT,
                'cached_tokens', SUM(cached_tokens)::BIGINT,
                'output_tokens', SUM(output_tokens)::BIGINT,
                'calls', COUNT(*)::BIGINT
            ) AS totals,
            SUM(latency_ms)::BIGINT AS latency_ms,
            BOOL_OR(estimated) AS estimated
        FROM token_usage
        WHERE run_id = p_run_id
        GROUP BY run_id
    ), turns AS (
        SELECT jsonb_agg(jsonb_build_object(
            'usage_id', id,
            'turn_index', turn_index,
            'ts', ts,
            'model', model,
            'input_tokens', input_tokens,
            'cached_tokens', cached_tokens,
            'output_tokens', output_tokens,
            'et', et::DOUBLE PRECISION,
            'et_coeff_m', et_coeff_m::DOUBLE PRECISION,
            'latency_ms', latency_ms,
            'status', status,
            'estimated', estimated,
            'ingest_source', ingest_source
        ) ORDER BY turn_index) AS items
        FROM token_usage
        WHERE run_id = p_run_id
    )
    SELECT jsonb_build_object(
        'run_id', summary.run_id,
        'started_at', summary.started_at,
        'ended_at', summary.ended_at,
        'team', summary.team,
        'user', summary.user_ref,
        'agent', summary.agent,
        'workflow', summary.workflow,
        'provider', summary.provider,
        'models', summary.models,
        'turn_count', summary.turn_count,
        'totals', summary.totals,
        'latency_ms', summary.latency_ms,
        'estimated', summary.estimated,
        'turns', turns.items
    )
    FROM summary CROSS JOIN turns;
$$;


--
-- Name: token_observability_runs(timestamp with time zone, timestamp with time zone, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_runs(p_from timestamp with time zone, p_to timestamp with time zone, p_limit integer) RETURNS TABLE(run_id text, started_at timestamp with time zone, ended_at timestamp with time zone, team text, "user" text, agent text, workflow text, provider text, models text[], turn_count integer, totals jsonb, latency_ms bigint, estimated boolean)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        usage.run_id,
        MIN(usage.ts),
        MAX(usage.ts),
        MIN(usage.team),
        MIN(usage.user_ref),
        MIN(usage.agent),
        MIN(usage.workflow),
        MIN(usage.provider),
        ARRAY_AGG(DISTINCT usage.model ORDER BY usage.model),
        COUNT(*)::INTEGER,
        jsonb_build_object(
            'et', SUM(usage.et)::DOUBLE PRECISION,
            'total_tokens', SUM(usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT,
            'input_tokens', SUM(usage.input_tokens)::BIGINT,
            'cached_tokens', SUM(usage.cached_tokens)::BIGINT,
            'output_tokens', SUM(usage.output_tokens)::BIGINT,
            'calls', COUNT(*)::BIGINT
        ),
        SUM(usage.latency_ms)::BIGINT,
        BOOL_OR(usage.estimated)
    FROM token_usage usage
    WHERE usage.ts >= p_from AND usage.ts < p_to
    GROUP BY usage.run_id
    ORDER BY MAX(usage.ts) DESC
    LIMIT p_limit;
$$;


--
-- Name: token_observability_trends(timestamp with time zone, timestamp with time zone, text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_trends(p_from timestamp with time zone, p_to timestamp with time zone, p_interval text, p_group_by text, p_timezone text) RETURNS TABLE(bucket_start timestamp with time zone, key text, label text, totals jsonb)
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
    IF p_interval NOT IN ('hour', 'day', 'week') THEN
        RAISE EXCEPTION 'Unsupported interval: %', p_interval;
    END IF;
    IF p_group_by NOT IN (
        'organization', 'department', 'project', 'agent', 'user', 'model', 'runtime', 'workflow', 'team'
    ) THEN
        RAISE EXCEPTION 'Unsupported grouping: %', p_group_by;
    END IF;

    RETURN QUERY
    SELECT
        date_trunc(p_interval, usage.ts AT TIME ZONE p_timezone) AT TIME ZONE p_timezone,
        CASE p_group_by
            WHEN 'organization' THEN usage.organization
            WHEN 'department' THEN usage.department
            WHEN 'project' THEN usage.project
            WHEN 'agent' THEN usage.agent
            WHEN 'user' THEN usage.user_ref
            WHEN 'model' THEN usage.model
            WHEN 'runtime' THEN usage.runtime
            WHEN 'workflow' THEN usage.workflow
            ELSE usage.team
        END,
        CASE p_group_by
            WHEN 'organization' THEN usage.organization
            WHEN 'department' THEN usage.department
            WHEN 'project' THEN usage.project
            WHEN 'agent' THEN usage.agent
            WHEN 'user' THEN usage.user_ref
            WHEN 'model' THEN usage.model
            WHEN 'runtime' THEN usage.runtime
            WHEN 'workflow' THEN usage.workflow
            ELSE usage.team
        END,
        jsonb_build_object(
            'et', SUM(usage.et)::DOUBLE PRECISION,
            'total_tokens', SUM(usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT,
            'input_tokens', SUM(usage.input_tokens)::BIGINT,
            'cached_tokens', SUM(usage.cached_tokens)::BIGINT,
            'output_tokens', SUM(usage.output_tokens)::BIGINT,
            'calls', COUNT(*)::BIGINT
        )
    FROM token_usage usage
    WHERE usage.ts >= p_from AND usage.ts < p_to
    GROUP BY 1, 2, 3
    ORDER BY 1, 2;
END;
$$;


--
-- Name: token_observability_workflow_metric(text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.token_observability_workflow_metric(p_workflow text, p_from timestamp with time zone, p_to timestamp with time zone) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT jsonb_build_object(
        'et', COALESCE(SUM(et), 0)::DOUBLE PRECISION,
        'total_tokens', COALESCE(SUM(input_tokens + cached_tokens + output_tokens), 0)::BIGINT,
        'input_tokens', COALESCE(SUM(input_tokens), 0)::BIGINT,
        'cached_tokens', COALESCE(SUM(cached_tokens), 0)::BIGINT,
        'output_tokens', COALESCE(SUM(output_tokens), 0)::BIGINT,
        'calls', COUNT(*)::BIGINT
    )
    FROM token_usage
    WHERE workflow = p_workflow AND ts >= p_from AND ts < p_to;
$$;


SET default_table_access_method = heap;

--
-- Name: anomaly_rule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.anomaly_rule (
    id uuid NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    metric text NOT NULL,
    threshold_mode text NOT NULL,
    threshold_value double precision NOT NULL,
    minimum_sample_size integer DEFAULT 1 NOT NULL,
    severity text NOT NULL,
    scope_type text DEFAULT 'global'::text NOT NULL,
    scope_id text,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text NOT NULL,
    CONSTRAINT anomaly_rule_agent_absolute_integer CHECK (((metric <> 'agent_request_count'::text) OR (threshold_mode <> 'absolute'::text) OR (threshold_value = floor(threshold_value)))),
    CONSTRAINT anomaly_rule_description_check CHECK ((length(description) <= 500)),
    CONSTRAINT anomaly_rule_metric_check CHECK ((metric = ANY (ARRAY['error_rate_percent'::text, 'request_latency_ms'::text, 'request_cost_usd'::text, 'agent_request_count'::text]))),
    CONSTRAINT anomaly_rule_metric_mode CHECK (((metric <> ALL (ARRAY['error_rate_percent'::text, 'request_latency_ms'::text])) OR (threshold_mode = 'absolute'::text))),
    CONSTRAINT anomaly_rule_minimum_sample_size_check CHECK (((minimum_sample_size >= 1) AND (minimum_sample_size <= 100000))),
    CONSTRAINT anomaly_rule_name_check CHECK (((length(btrim(name)) >= 1) AND (length(btrim(name)) <= 120))),
    CONSTRAINT anomaly_rule_percentile_range CHECK (((threshold_mode <> 'percentile'::text) OR ((threshold_value >= (1)::double precision) AND (threshold_value <= (100)::double precision)))),
    CONSTRAINT anomaly_rule_scope_consistency CHECK ((((scope_type = 'global'::text) AND (scope_id IS NULL)) OR ((scope_type <> 'global'::text) AND (scope_id IS NOT NULL) AND (length(btrim(scope_id)) > 0)))),
    CONSTRAINT anomaly_rule_scope_type_check CHECK ((scope_type = ANY (ARRAY['global'::text, 'organization'::text, 'department'::text, 'project'::text, 'agent'::text, 'model'::text, 'user'::text]))),
    CONSTRAINT anomaly_rule_severity_check CHECK ((severity = ANY (ARRAY['warning'::text, 'critical'::text]))),
    CONSTRAINT anomaly_rule_threshold_mode_check CHECK ((threshold_mode = ANY (ARRAY['absolute'::text, 'percentile'::text]))),
    CONSTRAINT anomaly_rule_threshold_value_check CHECK ((threshold_value > (0)::double precision))
);


--
-- Name: app_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_user (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    display_name text,
    password_hash text,
    role text DEFAULT 'member'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone,
    CONSTRAINT app_user_display_name_check CHECK (((display_name IS NULL) OR (length(TRIM(BOTH FROM display_name)) > 0))),
    CONSTRAINT app_user_email_check CHECK (((email = lower(email)) AND (email ~~ '%@%'::text))),
    CONSTRAINT app_user_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'member'::text])))
);


--
-- Name: assistant_conversation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_conversation (
    id uuid NOT NULL,
    owner_id text NOT NULL,
    title text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    title_source text DEFAULT 'question'::text NOT NULL,
    CONSTRAINT assistant_conversation_title_check CHECK (((length(title) >= 1) AND (length(title) <= 200))),
    CONSTRAINT assistant_conversation_title_source_check CHECK ((title_source = ANY (ARRAY['question'::text, 'model'::text, 'user'::text])))
);


--
-- Name: assistant_conversation_owner; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_conversation_owner (
    conversation_id uuid NOT NULL,
    created_by text NOT NULL
);


--
-- Name: assistant_setting; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_setting (
    id boolean DEFAULT true NOT NULL,
    model_id uuid,
    auto_title boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text,
    CONSTRAINT assistant_setting_id_check CHECK (id)
);


--
-- Name: assistant_turn; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_turn (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id uuid NOT NULL,
    "position" integer NOT NULL,
    question text NOT NULL,
    reply jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT assistant_turn_position_check CHECK (("position" >= 0))
);


--
-- Name: audit_finding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_finding (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    rule_id text NOT NULL,
    severity text NOT NULL,
    workflow text NOT NULL,
    run_id text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    assignee text,
    resolution_note text,
    suggestion text,
    CONSTRAINT audit_finding_severity_check CHECK ((severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text]))),
    CONSTRAINT audit_finding_status_check CHECK ((status = ANY (ARRAY['new'::text, 'acknowledged'::text, 'resolved'::text, 'ignored'::text])))
);


--
-- Name: budget_roll_forward; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.budget_roll_forward (
    period_start date NOT NULL,
    source_period_start date,
    scope_count integer DEFAULT 0 NOT NULL,
    rolled_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT budget_roll_forward_period_start_check CHECK ((EXTRACT(day FROM period_start) = (1)::numeric)),
    CONSTRAINT budget_roll_forward_scope_count_check CHECK ((scope_count >= 0))
);


--
-- Name: TABLE budget_roll_forward; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.budget_roll_forward IS 'One row per period recording that it has already inherited the previous period''s budgets.';


--
-- Name: department_enforcement; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.department_enforcement (
    department_id text NOT NULL,
    mode text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text NOT NULL,
    CONSTRAINT department_enforcement_mode_check CHECK ((mode = ANY (ARRAY['block'::text, 'audit'::text])))
);


--
-- Name: TABLE department_enforcement; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.department_enforcement IS 'Per-department enforcement mode for monthly Token budgets. A missing row means audit.';


--
-- Name: COLUMN department_enforcement.mode; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.department_enforcement.mode IS 'block = APIM may return 403 on exhausted allowance; audit = record only, always forward.';


--
-- Name: department_enforcement_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.department_enforcement_audit (
    id uuid NOT NULL,
    department_id text NOT NULL,
    previous_mode text,
    new_mode text NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by text NOT NULL,
    CONSTRAINT department_enforcement_audit_check CHECK (((previous_mode IS NULL) OR (previous_mode <> new_mode))),
    CONSTRAINT department_enforcement_audit_new_mode_check CHECK ((new_mode = ANY (ARRAY['block'::text, 'audit'::text]))),
    CONSTRAINT department_enforcement_audit_previous_mode_check CHECK ((previous_mode = ANY (ARRAY['block'::text, 'audit'::text])))
);


--
-- Name: TABLE department_enforcement_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.department_enforcement_audit IS 'Immutable history of enforcement mode changes. Turning enforcement off is more sensitive than editing a budget.';


--
-- Name: effective_gateway_release; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.effective_gateway_release (
    gateway_profile_id uuid NOT NULL,
    publication_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: gateway_profile; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gateway_profile (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    implementation text NOT NULL,
    base_url text,
    auth_type text DEFAULT 'none'::text NOT NULL,
    credential_ciphertext bytea,
    credential_hint text,
    enabled boolean DEFAULT true NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT gateway_profile_auth_type_check CHECK ((auth_type = ANY (ARRAY['none'::text, 'api_key'::text, 'bearer'::text, 'azure_ad'::text]))),
    CONSTRAINT gateway_profile_implementation_check CHECK ((implementation = ANY (ARRAY['apim'::text, 'litellm'::text, 'direct'::text])))
);


--
-- Name: COLUMN gateway_profile.credential_ciphertext; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.gateway_profile.credential_ciphertext IS 'Fernet ciphertext only. Plaintext credentials must never be stored or returned.';


--
-- Name: gateway_publication; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gateway_publication (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    gateway_profile_id uuid NOT NULL,
    generation bigint NOT NULL,
    desired_spec jsonb NOT NULL,
    desired_spec_sha256 text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    base_release_id uuid,
    apim_revision text,
    policy_sha256 text,
    resource_manifest jsonb DEFAULT '{}'::jsonb NOT NULL,
    error_code text,
    error_message text,
    attempt_count integer DEFAULT 0 NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    publication_kind text DEFAULT 'model_add'::text NOT NULL,
    CONSTRAINT gateway_publication_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT gateway_publication_desired_spec_sha256_check CHECK ((desired_spec_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT gateway_publication_policy_sha256_check CHECK (((policy_sha256 IS NULL) OR (policy_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT gateway_publication_publication_kind_check CHECK ((publication_kind = ANY (ARRAY['model_add'::text, 'model_remove'::text, 'credential_rotation'::text]))),
    CONSTRAINT gateway_publication_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'validating'::text, 'provisioning'::text, 'building_revision'::text, 'verifying'::text, 'awaiting_authorization'::text, 'promoting'::text, 'active'::text, 'failed'::text, 'superseded'::text, 'rolling_back'::text, 'rolled_back'::text])))
);


--
-- Name: TABLE gateway_publication; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.gateway_publication IS 'Immutable desired gateway releases. Existing registry rows remain the effective state until a release is promoted.';


--
-- Name: COLUMN gateway_publication.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.gateway_publication.status IS 'awaiting_authorization pauses a candidate without polling until an administrator grants the provider role and explicitly resumes verification.';


--
-- Name: COLUMN gateway_publication.resource_manifest; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.gateway_publication.resource_manifest IS 'Non-secret immutable APIM resource identifiers required for verification and rollback.';


--
-- Name: COLUMN gateway_publication.publication_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.gateway_publication.publication_kind IS 'Model additions materialize a Registry model; model removals publish a subtractive APIM revision before physically deleting the Registry row; credential rotations switch a managed Runtime credential after promotion.';


--
-- Name: gateway_publication_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gateway_publication_audit (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    publication_id uuid NOT NULL,
    from_status text,
    to_status text NOT NULL,
    actor text NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: gateway_publication_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gateway_publication_outbox (
    id bigint NOT NULL,
    publication_id uuid NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error_code text,
    last_error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT gateway_publication_outbox_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT gateway_publication_outbox_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'leased'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: TABLE gateway_publication_outbox; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.gateway_publication_outbox IS 'Durable work queue for the isolated APIM control-plane Function; never read on the inference path.';


--
-- Name: gateway_publication_outbox_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gateway_publication_outbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gateway_publication_outbox_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gateway_publication_outbox_id_seq OWNED BY public.gateway_publication_outbox.id;


--
-- Name: gateway_publication_secret; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gateway_publication_secret (
    publication_id uuid NOT NULL,
    credential_ciphertext bytea NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE gateway_publication_secret; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.gateway_publication_secret IS 'Encrypted one-time credentials for APIM publication. Deleted after Named Value provisioning or terminal failure.';


--
-- Name: managed_model; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.managed_model (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider_id uuid NOT NULL,
    runtime_id uuid NOT NULL,
    model_key text NOT NULL,
    display_name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    capabilities text[] DEFAULT ARRAY['chat'::text] NOT NULL,
    context_window integer,
    input_cost_per_million numeric(18,8),
    output_cost_per_million numeric(18,8),
    allowed_roles text[] DEFAULT ARRAY['owner'::text, 'admin'::text, 'member'::text] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    cached_cost_per_million numeric(18,8),
    cache_write_cost_per_million numeric(18,8),
    CONSTRAINT managed_model_cache_write_cost_per_million_check CHECK ((cache_write_cost_per_million >= (0)::numeric)),
    CONSTRAINT managed_model_cached_cost_per_million_check CHECK ((cached_cost_per_million >= (0)::numeric)),
    CONSTRAINT managed_model_context_window_check CHECK ((context_window > 0)),
    CONSTRAINT managed_model_input_cost_per_million_check CHECK ((input_cost_per_million >= (0)::numeric)),
    CONSTRAINT managed_model_output_cost_per_million_check CHECK ((output_cost_per_million >= (0)::numeric))
);


--
-- Name: COLUMN managed_model.cached_cost_per_million; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.managed_model.cached_cost_per_million IS 'USD per million cached input tokens. NULL falls back to input_cost_per_million.';


--
-- Name: COLUMN managed_model.cache_write_cost_per_million; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.managed_model.cache_write_cost_per_million IS 'USD per million cache-write tokens. NULL falls back to cached_cost_per_million.';


--
-- Name: managed_model_metadata; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.managed_model_metadata (
    model_id uuid NOT NULL,
    family_key text DEFAULT 'generic'::text NOT NULL,
    upstream_model_id text NOT NULL,
    assignment_required boolean DEFAULT false NOT NULL,
    publication_id uuid,
    CONSTRAINT managed_model_metadata_family_key_check CHECK ((family_key = ANY (ARRAY['generic'::text, 'claude'::text, 'copilot'::text, 'openai'::text])))
);


--
-- Name: TABLE managed_model_metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.managed_model_metadata IS 'Control-plane metadata kept outside managed_model so pre-032 SELECT * response shapes remain stable.';


--
-- Name: COLUMN managed_model_metadata.assignment_required; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.managed_model_metadata.assignment_required IS 'Dynamic gateway models deny callers without an explicit model policy; legacy models preserve compatibility.';


--
-- Name: model_coefficient; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_coefficient (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider text NOT NULL,
    model_pattern text NOT NULL,
    coefficient_m numeric(18,8) NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_coefficient_check CHECK (((valid_to IS NULL) OR (valid_to > valid_from))),
    CONSTRAINT model_coefficient_coefficient_m_check CHECK ((coefficient_m > (0)::numeric))
);


--
-- Name: model_provider; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_provider (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    provider_kind text NOT NULL,
    endpoint_url text,
    auth_type text DEFAULT 'none'::text NOT NULL,
    credential_ciphertext bytea,
    credential_hint text,
    enabled boolean DEFAULT true NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_provider_auth_type_check CHECK ((auth_type = ANY (ARRAY['none'::text, 'api_key'::text, 'bearer'::text, 'azure_ad'::text]))),
    CONSTRAINT model_provider_provider_kind_check CHECK ((provider_kind = ANY (ARRAY['github'::text, 'anthropic'::text, 'microsoft_foundry'::text, 'openai_compatible'::text])))
);


--
-- Name: TABLE model_provider; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.model_provider IS 'Zero-to-many provider integrations. Fresh deployments do not require a preconfigured model provider.';


--
-- Name: COLUMN model_provider.credential_ciphertext; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.model_provider.credential_ciphertext IS 'Fernet ciphertext only. Plaintext credentials must never be stored or returned.';


--
-- Name: model_provider_metadata; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_provider_metadata (
    provider_id uuid NOT NULL,
    brand_key text DEFAULT 'generic'::text NOT NULL,
    CONSTRAINT model_provider_metadata_brand_key_check CHECK ((brand_key = ANY (ARRAY['generic'::text, 'amazon_bedrock'::text, 'anthropic'::text, 'azure_databricks'::text, 'github'::text, 'microsoft'::text, 'microsoft_foundry'::text, 'openai'::text])))
);


--
-- Name: TABLE model_provider_metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.model_provider_metadata IS 'Control-plane metadata kept outside model_provider so pre-032 SELECT * response shapes remain stable.';


--
-- Name: model_runtime; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_runtime (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider_id uuid NOT NULL,
    gateway_profile_id uuid,
    name text NOT NULL,
    runtime_kind text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    allowed_roles text[] DEFAULT ARRAY['owner'::text, 'admin'::text, 'member'::text] NOT NULL,
    health_status text DEFAULT 'unknown'::text NOT NULL,
    health_message text,
    last_checked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_runtime_health_status_check CHECK ((health_status = ANY (ARRAY['unknown'::text, 'available'::text, 'unavailable'::text]))),
    CONSTRAINT model_runtime_runtime_kind_check CHECK ((runtime_kind = ANY (ARRAY['copilot_cli'::text, 'cloud_code_cli'::text, 'foundry'::text, 'openai_compatible'::text])))
);


--
-- Name: model_runtime_metadata; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_runtime_metadata (
    runtime_id uuid NOT NULL,
    brand_key text DEFAULT 'generic'::text NOT NULL,
    CONSTRAINT model_runtime_metadata_brand_key_check CHECK ((brand_key = ANY (ARRAY['generic'::text, 'amazon_bedrock'::text, 'anthropic'::text, 'azure_databricks'::text, 'github'::text, 'microsoft'::text, 'microsoft_foundry'::text, 'openai'::text])))
);


--
-- Name: TABLE model_runtime_metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.model_runtime_metadata IS 'Control-plane metadata kept outside model_runtime so pre-032 SELECT * response shapes remain stable.';


--
-- Name: optimization_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.optimization_event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    workflow text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    label text NOT NULL,
    notes text,
    comparison_window_days integer DEFAULT 7 NOT NULL,
    CONSTRAINT optimization_event_comparison_window_days_check CHECK (((comparison_window_days >= 1) AND (comparison_window_days <= 90)))
);


--
-- Name: pinned_chart; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pinned_chart (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    original_question text NOT NULL,
    chart jsonb NOT NULL,
    "position" integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    report_id uuid NOT NULL,
    CONSTRAINT pinned_chart_position_check CHECK (("position" >= 0))
);


--
-- Name: pinned_report; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pinned_report (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id text NOT NULL,
    title text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    "position" integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pinned_report_position_check CHECK (("position" >= 0)),
    CONSTRAINT pinned_report_title_check CHECK (((length(title) >= 1) AND (length(title) <= 120)))
);


--
-- Name: pinned_report_access; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pinned_report_access (
    report_id uuid NOT NULL,
    created_by text NOT NULL,
    visibility text DEFAULT 'private'::text NOT NULL,
    CONSTRAINT pinned_report_access_visibility_check CHECK ((visibility = ANY (ARRAY['private'::text, 'public'::text])))
);


--
-- Name: schema_migration; Type: TABLE; Schema: public; Owner: -
--



--
-- Name: token_budget; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.token_budget (
    period_start date NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    parent_scope_id text,
    token_limit bigint NOT NULL,
    warning_threshold_percent smallint DEFAULT 80 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text NOT NULL,
    CONSTRAINT token_budget_check CHECK ((((scope_type = 'organization'::text) AND (parent_scope_id IS NULL)) OR ((scope_type = ANY (ARRAY['department'::text, 'user'::text])) AND (parent_scope_id IS NOT NULL)))),
    CONSTRAINT token_budget_period_start_check CHECK ((EXTRACT(day FROM period_start) = (1)::numeric)),
    CONSTRAINT token_budget_scope_type_check CHECK ((scope_type = ANY (ARRAY['organization'::text, 'department'::text, 'user'::text]))),
    CONSTRAINT token_budget_token_limit_check CHECK ((token_limit > 0)),
    CONSTRAINT token_budget_warning_threshold_percent_check CHECK (((warning_threshold_percent >= 1) AND (warning_threshold_percent <= 100)))
);


--
-- Name: TABLE token_budget; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.token_budget IS 'Monthly soft Token budgets for enterprise organization, department, and user scopes.';


--
-- Name: token_budget_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.token_budget_audit (
    id uuid NOT NULL,
    period_start date NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    action text NOT NULL,
    previous_token_limit bigint,
    new_token_limit bigint,
    previous_warning_threshold_percent smallint,
    new_warning_threshold_percent smallint,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by text NOT NULL,
    CONSTRAINT token_budget_audit_action_check CHECK ((action = ANY (ARRAY['assigned'::text, 'updated'::text, 'removed'::text]))),
    CONSTRAINT token_budget_audit_new_token_limit_check CHECK ((new_token_limit > 0)),
    CONSTRAINT token_budget_audit_new_warning_threshold_percent_check CHECK (((new_warning_threshold_percent >= 1) AND (new_warning_threshold_percent <= 100))),
    CONSTRAINT token_budget_audit_previous_token_limit_check CHECK ((previous_token_limit > 0)),
    CONSTRAINT token_budget_audit_previous_warning_threshold_percent_check CHECK (((previous_warning_threshold_percent >= 1) AND (previous_warning_threshold_percent <= 100))),
    CONSTRAINT token_budget_audit_scope_type_check CHECK ((scope_type = ANY (ARRAY['organization'::text, 'department'::text, 'user'::text])))
);


--
-- Name: TABLE token_budget_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.token_budget_audit IS 'Immutable history of Token budget assignments, updates, and removals.';


--
-- Name: token_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.token_usage (
    id text NOT NULL,
    ts timestamp with time zone NOT NULL,
    team text DEFAULT 'unattributed'::text NOT NULL,
    user_ref text DEFAULT 'unattributed'::text NOT NULL,
    agent text DEFAULT 'unattributed'::text NOT NULL,
    workflow text DEFAULT 'unattributed'::text NOT NULL,
    run_id text DEFAULT 'unattributed'::text NOT NULL,
    turn_index integer NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    input_tokens bigint NOT NULL,
    cached_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    et numeric(24,6) NOT NULL,
    et_coeff_m numeric(18,8) NOT NULL,
    latency_ms bigint NOT NULL,
    status text NOT NULL,
    estimated boolean DEFAULT false NOT NULL,
    ingest_source text NOT NULL,
    ingest_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    organization text DEFAULT 'unattributed'::text NOT NULL,
    department text DEFAULT 'unattributed'::text NOT NULL,
    project text DEFAULT 'unattributed'::text NOT NULL,
    runtime text DEFAULT 'unattributed'::text NOT NULL,
    request_id text NOT NULL,
    correlation_id text NOT NULL,
    organization_id text DEFAULT 'unattributed'::text NOT NULL,
    department_id text DEFAULT 'unattributed'::text NOT NULL,
    project_id text DEFAULT 'unattributed'::text NOT NULL,
    user_id text DEFAULT 'unattributed'::text NOT NULL,
    agent_id text DEFAULT 'unattributed'::text NOT NULL,
    model_id text DEFAULT 'unattributed'::text NOT NULL,
    request_source text DEFAULT 'unattributed'::text NOT NULL,
    status_code integer NOT NULL,
    estimated_cost numeric(18,8) DEFAULT 0 NOT NULL,
    error_message text,
    reconciled_at timestamp with time zone,
    input_price_per_million numeric(18,8),
    cached_price_per_million numeric(18,8),
    output_price_per_million numeric(18,8),
    cache_write_tokens bigint DEFAULT 0 NOT NULL,
    cache_write_price_per_million numeric(18,8),
    budget_admission text,
    model_admission text,
    CONSTRAINT token_usage_cache_write_price_per_million_check CHECK ((cache_write_price_per_million >= (0)::numeric)),
    CONSTRAINT token_usage_cache_write_tokens_check CHECK ((cache_write_tokens >= 0)),
    CONSTRAINT token_usage_cache_write_within_cached CHECK ((cache_write_tokens <= cached_tokens)),
    CONSTRAINT token_usage_cached_price_per_million_check CHECK ((cached_price_per_million >= (0)::numeric)),
    CONSTRAINT token_usage_cached_tokens_check CHECK ((cached_tokens >= 0)),
    CONSTRAINT token_usage_estimated_cost_check CHECK ((estimated_cost >= (0)::numeric)),
    CONSTRAINT token_usage_et_check CHECK ((et >= (0)::numeric)),
    CONSTRAINT token_usage_et_coeff_m_check CHECK ((et_coeff_m > (0)::numeric)),
    CONSTRAINT token_usage_ingest_source_check CHECK ((ingest_source = ANY (ARRAY['eventhub'::text, 'backfill'::text, 'gateway'::text, 'policy'::text, 'copilot_cli'::text]))),
    CONSTRAINT token_usage_input_price_per_million_check CHECK ((input_price_per_million >= (0)::numeric)),
    CONSTRAINT token_usage_input_tokens_check CHECK ((input_tokens >= 0)),
    CONSTRAINT token_usage_latency_ms_check CHECK ((latency_ms >= 0)),
    CONSTRAINT token_usage_output_price_per_million_check CHECK ((output_price_per_million >= (0)::numeric)),
    CONSTRAINT token_usage_output_tokens_check CHECK ((output_tokens >= 0)),
    CONSTRAINT token_usage_provider_check CHECK ((length(TRIM(BOTH FROM provider)) > 0)),
    CONSTRAINT token_usage_status_code_check CHECK (((status_code >= 100) AND (status_code <= 599))),
    CONSTRAINT token_usage_turn_index_check CHECK ((turn_index > 0))
);


--
-- Name: TABLE token_usage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.token_usage IS 'Token counts and attribution metadata only. Prompt and completion content are prohibited.';


--
-- Name: COLUMN token_usage.et_coeff_m; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.et_coeff_m IS 'Immutable coefficient snapshot used when ET was computed.';


--
-- Name: COLUMN token_usage.ingest_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.ingest_source IS 'Processing stage that persisted the record. Policy denials occur before APIM/provider
invocation. copilot_cli rows are parsed from local GitHub Copilot CLI logs, which carry
GitHub''s own per-request metering block.';


--
-- Name: COLUMN token_usage.request_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.request_id IS 'Caller-visible request identifier for exact record lookup.';


--
-- Name: COLUMN token_usage.correlation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.correlation_id IS 'APIM correlation identifier used to trace the gateway request.';


--
-- Name: COLUMN token_usage.error_message; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.error_message IS 'Sanitized failure summary. Prompt and completion content are prohibited.';


--
-- Name: COLUMN token_usage.reconciled_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.reconciled_at IS 'Set when a previously unmeasured row received its real token breakdown from the gateway LLM log.';


--
-- Name: COLUMN token_usage.input_price_per_million; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.input_price_per_million IS 'USD per million input tokens applied when this row was priced. NULL means the request could not be priced.';


--
-- Name: COLUMN token_usage.cached_price_per_million; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.cached_price_per_million IS 'USD per million cached input tokens applied when this row was priced.';


--
-- Name: COLUMN token_usage.output_price_per_million; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.output_price_per_million IS 'USD per million output tokens applied when this row was priced.';


--
-- Name: COLUMN token_usage.cache_write_tokens; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.cache_write_tokens IS 'Cache-write subset of cached_tokens. Cache reads are cached_tokens - cache_write_tokens. 0 on rows ingested before the split was reported.';


--
-- Name: COLUMN token_usage.cache_write_price_per_million; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.token_usage.cache_write_price_per_million IS 'USD per million cache-write tokens applied when this row was priced.';


--
-- Name: usage_reconciliation_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usage_reconciliation_state (
    source text NOT NULL,
    watermark timestamp with time zone NOT NULL,
    last_run_at timestamp with time zone DEFAULT now() NOT NULL,
    last_matched bigint DEFAULT 0 NOT NULL,
    last_scanned bigint DEFAULT 0 NOT NULL,
    CONSTRAINT usage_reconciliation_state_last_matched_check CHECK ((last_matched >= 0)),
    CONSTRAINT usage_reconciliation_state_last_scanned_check CHECK ((last_scanned >= 0))
);


--
-- Name: TABLE usage_reconciliation_state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.usage_reconciliation_state IS 'Per-source watermark for scheduled usage reconciliation. Bounds the query window so each run reads only new gateway telemetry.';


--
-- Name: user_model_access; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_model_access (
    user_id text NOT NULL,
    model_id uuid NOT NULL
);


--
-- Name: TABLE user_model_access; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_model_access IS 'Models allowed by an explicitly configured user model access policy.';


--
-- Name: user_model_access_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_model_access_audit (
    id uuid NOT NULL,
    user_id text NOT NULL,
    previous_model_ids uuid[] DEFAULT ARRAY[]::uuid[] NOT NULL,
    new_model_ids uuid[] DEFAULT ARRAY[]::uuid[] NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by text NOT NULL
);


--
-- Name: TABLE user_model_access_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_model_access_audit IS 'Immutable history of per-user model access policy replacements.';


--
-- Name: user_model_policy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_model_policy (
    user_id text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text NOT NULL
);


--
-- Name: TABLE user_model_policy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_model_policy IS 'Durable per-user model access policy. A row with no access children explicitly denies all models.';


--
-- Name: user_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_session (
    token_sha256 text NOT NULL,
    user_id uuid NOT NULL,
    method text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT user_session_check CHECK ((expires_at > created_at)),
    CONSTRAINT user_session_method_check CHECK ((method = ANY (ARRAY['password'::text, 'entra'::text])))
);


--
-- Name: gateway_publication_outbox id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication_outbox ALTER COLUMN id SET DEFAULT nextval('public.gateway_publication_outbox_id_seq'::regclass);


--
-- Data for Name: anomaly_rule; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: app_user; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: assistant_conversation; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: assistant_conversation_owner; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: assistant_setting; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: assistant_turn; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: audit_finding; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: budget_roll_forward; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: department_enforcement; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: department_enforcement_audit; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: effective_gateway_release; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: gateway_profile; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: gateway_publication; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: gateway_publication_audit; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: gateway_publication_outbox; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: gateway_publication_secret; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: managed_model; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: managed_model_metadata; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: model_coefficient; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: model_provider; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: model_provider_metadata; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: model_runtime; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: model_runtime_metadata; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: optimization_event; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: pinned_chart; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: pinned_report; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: pinned_report_access; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: schema_migration; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: token_budget; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: token_budget_audit; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: token_usage; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: usage_reconciliation_state; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: user_model_access; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: user_model_access_audit; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: user_model_policy; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: user_session; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Name: gateway_publication_outbox_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.gateway_publication_outbox_id_seq', 1, false);


--
-- Name: anomaly_rule anomaly_rule_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.anomaly_rule
    ADD CONSTRAINT anomaly_rule_pkey PRIMARY KEY (id);


--
-- Name: app_user app_user_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT app_user_email_key UNIQUE (email);


--
-- Name: app_user app_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT app_user_pkey PRIMARY KEY (id);


--
-- Name: assistant_conversation_owner assistant_conversation_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_conversation_owner
    ADD CONSTRAINT assistant_conversation_owner_pkey PRIMARY KEY (conversation_id);


--
-- Name: assistant_conversation assistant_conversation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_conversation
    ADD CONSTRAINT assistant_conversation_pkey PRIMARY KEY (id);


--
-- Name: assistant_setting assistant_setting_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_setting
    ADD CONSTRAINT assistant_setting_pkey PRIMARY KEY (id);


--
-- Name: assistant_turn assistant_turn_conversation_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_turn
    ADD CONSTRAINT assistant_turn_conversation_id_position_key UNIQUE (conversation_id, "position");


--
-- Name: assistant_turn assistant_turn_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_turn
    ADD CONSTRAINT assistant_turn_pkey PRIMARY KEY (id);


--
-- Name: audit_finding audit_finding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_finding
    ADD CONSTRAINT audit_finding_pkey PRIMARY KEY (id);


--
-- Name: budget_roll_forward budget_roll_forward_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.budget_roll_forward
    ADD CONSTRAINT budget_roll_forward_pkey PRIMARY KEY (period_start);


--
-- Name: department_enforcement_audit department_enforcement_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.department_enforcement_audit
    ADD CONSTRAINT department_enforcement_audit_pkey PRIMARY KEY (id);


--
-- Name: department_enforcement department_enforcement_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.department_enforcement
    ADD CONSTRAINT department_enforcement_pkey PRIMARY KEY (department_id);


--
-- Name: effective_gateway_release effective_gateway_release_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.effective_gateway_release
    ADD CONSTRAINT effective_gateway_release_pkey PRIMARY KEY (gateway_profile_id);


--
-- Name: effective_gateway_release effective_gateway_release_publication_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.effective_gateway_release
    ADD CONSTRAINT effective_gateway_release_publication_id_key UNIQUE (publication_id);


--
-- Name: gateway_profile gateway_profile_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_profile
    ADD CONSTRAINT gateway_profile_name_key UNIQUE (name);


--
-- Name: gateway_profile gateway_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_profile
    ADD CONSTRAINT gateway_profile_pkey PRIMARY KEY (id);


--
-- Name: gateway_publication_audit gateway_publication_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication_audit
    ADD CONSTRAINT gateway_publication_audit_pkey PRIMARY KEY (id);


--
-- Name: gateway_publication gateway_publication_gateway_profile_id_generation_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication
    ADD CONSTRAINT gateway_publication_gateway_profile_id_generation_key UNIQUE (gateway_profile_id, generation);


--
-- Name: gateway_publication_outbox gateway_publication_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication_outbox
    ADD CONSTRAINT gateway_publication_outbox_pkey PRIMARY KEY (id);


--
-- Name: gateway_publication gateway_publication_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication
    ADD CONSTRAINT gateway_publication_pkey PRIMARY KEY (id);


--
-- Name: gateway_publication_secret gateway_publication_secret_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication_secret
    ADD CONSTRAINT gateway_publication_secret_pkey PRIMARY KEY (publication_id);


--
-- Name: managed_model_metadata managed_model_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model_metadata
    ADD CONSTRAINT managed_model_metadata_pkey PRIMARY KEY (model_id);


--
-- Name: managed_model managed_model_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model
    ADD CONSTRAINT managed_model_pkey PRIMARY KEY (id);


--
-- Name: managed_model managed_model_runtime_id_model_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model
    ADD CONSTRAINT managed_model_runtime_id_model_key_key UNIQUE (runtime_id, model_key);


--
-- Name: model_coefficient model_coefficient_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_coefficient
    ADD CONSTRAINT model_coefficient_pkey PRIMARY KEY (id);


--
-- Name: model_coefficient model_coefficient_provider_model_pattern_valid_from_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_coefficient
    ADD CONSTRAINT model_coefficient_provider_model_pattern_valid_from_key UNIQUE (provider, model_pattern, valid_from);


--
-- Name: model_provider_metadata model_provider_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_provider_metadata
    ADD CONSTRAINT model_provider_metadata_pkey PRIMARY KEY (provider_id);


--
-- Name: model_provider model_provider_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_provider
    ADD CONSTRAINT model_provider_name_key UNIQUE (name);


--
-- Name: model_provider model_provider_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_provider
    ADD CONSTRAINT model_provider_pkey PRIMARY KEY (id);


--
-- Name: model_runtime model_runtime_id_provider_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runtime
    ADD CONSTRAINT model_runtime_id_provider_unique UNIQUE (id, provider_id);


--
-- Name: model_runtime_metadata model_runtime_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runtime_metadata
    ADD CONSTRAINT model_runtime_metadata_pkey PRIMARY KEY (runtime_id);


--
-- Name: model_runtime model_runtime_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runtime
    ADD CONSTRAINT model_runtime_name_key UNIQUE (name);


--
-- Name: model_runtime model_runtime_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runtime
    ADD CONSTRAINT model_runtime_pkey PRIMARY KEY (id);


--
-- Name: optimization_event optimization_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.optimization_event
    ADD CONSTRAINT optimization_event_pkey PRIMARY KEY (id);


--
-- Name: pinned_chart pinned_chart_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pinned_chart
    ADD CONSTRAINT pinned_chart_pkey PRIMARY KEY (id);


--
-- Name: pinned_report_access pinned_report_access_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pinned_report_access
    ADD CONSTRAINT pinned_report_access_pkey PRIMARY KEY (report_id);


--
-- Name: pinned_report pinned_report_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pinned_report
    ADD CONSTRAINT pinned_report_pkey PRIMARY KEY (id);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--



--
-- Name: token_budget_audit token_budget_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.token_budget_audit
    ADD CONSTRAINT token_budget_audit_pkey PRIMARY KEY (id);


--
-- Name: token_budget token_budget_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.token_budget
    ADD CONSTRAINT token_budget_pkey PRIMARY KEY (period_start, scope_type, scope_id);


--
-- Name: token_usage token_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.token_usage
    ADD CONSTRAINT token_usage_pkey PRIMARY KEY (id);


--
-- Name: usage_reconciliation_state usage_reconciliation_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_reconciliation_state
    ADD CONSTRAINT usage_reconciliation_state_pkey PRIMARY KEY (source);


--
-- Name: user_model_access_audit user_model_access_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_model_access_audit
    ADD CONSTRAINT user_model_access_audit_pkey PRIMARY KEY (id);


--
-- Name: user_model_access user_model_access_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_model_access
    ADD CONSTRAINT user_model_access_pkey PRIMARY KEY (user_id, model_id);


--
-- Name: user_model_policy user_model_policy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_model_policy
    ADD CONSTRAINT user_model_policy_pkey PRIMARY KEY (user_id);


--
-- Name: user_session user_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_session
    ADD CONSTRAINT user_session_pkey PRIMARY KEY (token_sha256);


--
-- Name: anomaly_rule_enabled_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX anomaly_rule_enabled_idx ON public.anomaly_rule USING btree (enabled, metric);


--
-- Name: assistant_conversation_owner_creator_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX assistant_conversation_owner_creator_idx ON public.assistant_conversation_owner USING btree (created_by, conversation_id);


--
-- Name: assistant_conversation_owner_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX assistant_conversation_owner_idx ON public.assistant_conversation USING btree (owner_id, updated_at DESC);


--
-- Name: assistant_turn_conversation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX assistant_turn_conversation_idx ON public.assistant_turn USING btree (conversation_id, "position");


--
-- Name: audit_finding_status_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_finding_status_created_idx ON public.audit_finding USING btree (status, created_at DESC);


--
-- Name: department_enforcement_audit_department_changed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX department_enforcement_audit_department_changed_idx ON public.department_enforcement_audit USING btree (department_id, changed_at DESC);


--
-- Name: gateway_profile_one_default_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX gateway_profile_one_default_idx ON public.gateway_profile USING btree (is_default) WHERE is_default;


--
-- Name: gateway_publication_audit_publication_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX gateway_publication_audit_publication_idx ON public.gateway_publication_audit USING btree (publication_id, created_at);


--
-- Name: gateway_publication_one_in_flight_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX gateway_publication_one_in_flight_idx ON public.gateway_publication USING btree (gateway_profile_id) WHERE (status = ANY (ARRAY['queued'::text, 'validating'::text, 'provisioning'::text, 'building_revision'::text, 'verifying'::text, 'awaiting_authorization'::text, 'promoting'::text, 'rolling_back'::text]));


--
-- Name: gateway_publication_one_reusable_spec_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX gateway_publication_one_reusable_spec_idx ON public.gateway_publication USING btree (gateway_profile_id, desired_spec_sha256) WHERE (status <> ALL (ARRAY['superseded'::text, 'rolled_back'::text]));


--
-- Name: INDEX gateway_publication_one_reusable_spec_idx; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.gateway_publication_one_reusable_spec_idx IS 'Deduplicates a live/retryable desired state while allowing a superseded or rolled-back release to be published again.';


--
-- Name: gateway_publication_outbox_one_open_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX gateway_publication_outbox_one_open_idx ON public.gateway_publication_outbox USING btree (publication_id) WHERE (status = ANY (ARRAY['queued'::text, 'leased'::text]));


--
-- Name: gateway_publication_outbox_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX gateway_publication_outbox_ready_idx ON public.gateway_publication_outbox USING btree (available_at, id) WHERE (status = 'queued'::text);


--
-- Name: gateway_publication_recent_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX gateway_publication_recent_idx ON public.gateway_publication USING btree (gateway_profile_id, created_at DESC);


--
-- Name: idx_token_usage_budget_admission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_token_usage_budget_admission ON public.token_usage USING btree (ts DESC) WHERE ((budget_admission IS NOT NULL) AND (budget_admission <> 'ok'::text));


--
-- Name: idx_token_usage_model_admission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_token_usage_model_admission ON public.token_usage USING btree (ts DESC) WHERE ((model_admission IS NOT NULL) AND (model_admission <> 'ok'::text));


--
-- Name: idx_token_usage_user_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_token_usage_user_identity ON public.token_usage USING btree (user_id, ts DESC) WHERE (user_id <> 'unattributed'::text);


--
-- Name: managed_model_global_model_key_ci_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX managed_model_global_model_key_ci_idx ON public.managed_model USING btree (lower(model_key));


--
-- Name: managed_model_one_default_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX managed_model_one_default_idx ON public.managed_model USING btree (is_default) WHERE is_default;


--
-- Name: managed_model_provider_runtime_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX managed_model_provider_runtime_idx ON public.managed_model USING btree (provider_id, runtime_id);


--
-- Name: model_runtime_one_default_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX model_runtime_one_default_idx ON public.model_runtime USING btree (is_default) WHERE is_default;


--
-- Name: model_runtime_provider_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX model_runtime_provider_idx ON public.model_runtime USING btree (provider_id);


--
-- Name: optimization_event_workflow_occurred_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX optimization_event_workflow_occurred_idx ON public.optimization_event USING btree (workflow, occurred_at DESC);


--
-- Name: pinned_chart_report_position_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pinned_chart_report_position_idx ON public.pinned_chart USING btree (report_id, "position", created_at);


--
-- Name: pinned_chart_report_query_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX pinned_chart_report_query_idx ON public.pinned_chart USING btree (report_id, ((chart -> 'query'::text)));


--
-- Name: pinned_report_access_creator_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pinned_report_access_creator_idx ON public.pinned_report_access USING btree (created_by, report_id);


--
-- Name: pinned_report_access_public_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pinned_report_access_public_idx ON public.pinned_report_access USING btree (report_id) WHERE (visibility = 'public'::text);


--
-- Name: pinned_report_owner_position_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pinned_report_owner_position_idx ON public.pinned_report USING btree (owner_id, "position", created_at);


--
-- Name: token_budget_audit_period_changed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_budget_audit_period_changed_idx ON public.token_budget_audit USING btree (period_start, changed_at DESC);


--
-- Name: token_budget_parent_period_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_budget_parent_period_idx ON public.token_budget USING btree (period_start, parent_scope_id, scope_type);


--
-- Name: token_usage_business_dimensions_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_business_dimensions_ts_idx ON public.token_usage USING btree (organization, department, project, user_ref, runtime, ts DESC);


--
-- Name: token_usage_correlation_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_correlation_id_idx ON public.token_usage USING btree (correlation_id);


--
-- Name: token_usage_dimensions_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_dimensions_ts_idx ON public.token_usage USING btree (team, agent, model, ts DESC);


--
-- Name: token_usage_enterprise_dimensions_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_enterprise_dimensions_ts_idx ON public.token_usage USING btree (organization_id, department_id, project_id, agent_id, model_id, user_id, ts DESC);


--
-- Name: token_usage_pending_reconciliation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_pending_reconciliation_idx ON public.token_usage USING btree (ts) WHERE (estimated AND (reconciled_at IS NULL));


--
-- Name: token_usage_request_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX token_usage_request_id_idx ON public.token_usage USING btree (request_id);


--
-- Name: token_usage_run_turn_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_run_turn_idx ON public.token_usage USING btree (run_id, turn_index);


--
-- Name: token_usage_status_cost_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_status_cost_ts_idx ON public.token_usage USING btree (status_code, estimated_cost DESC, ts DESC);


--
-- Name: token_usage_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_ts_idx ON public.token_usage USING btree (ts DESC);


--
-- Name: token_usage_workflow_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX token_usage_workflow_ts_idx ON public.token_usage USING btree (workflow, ts DESC);


--
-- Name: user_model_access_audit_user_changed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_model_access_audit_user_changed_idx ON public.user_model_access_audit USING btree (user_id, changed_at DESC);


--
-- Name: user_model_access_model_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_model_access_model_idx ON public.user_model_access USING btree (model_id, user_id);


--
-- Name: user_session_expires_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_session_expires_idx ON public.user_session USING btree (expires_at);


--
-- Name: user_session_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_session_user_idx ON public.user_session USING btree (user_id);


--
-- Name: assistant_conversation assistant_conversation_owner_compat; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER assistant_conversation_owner_compat AFTER INSERT ON public.assistant_conversation FOR EACH ROW EXECUTE FUNCTION public.create_assistant_conversation_owner();


--
-- Name: pinned_report pinned_report_access_compat; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER pinned_report_access_compat AFTER INSERT ON public.pinned_report FOR EACH ROW EXECUTE FUNCTION public.create_pinned_report_access();


--
-- Name: assistant_conversation_owner assistant_conversation_owner_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_conversation_owner
    ADD CONSTRAINT assistant_conversation_owner_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.assistant_conversation(id) ON DELETE CASCADE;


--
-- Name: assistant_setting assistant_setting_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_setting
    ADD CONSTRAINT assistant_setting_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.managed_model(id) ON DELETE SET NULL;


--
-- Name: assistant_turn assistant_turn_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_turn
    ADD CONSTRAINT assistant_turn_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.assistant_conversation(id) ON DELETE CASCADE;


--
-- Name: effective_gateway_release effective_gateway_release_gateway_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.effective_gateway_release
    ADD CONSTRAINT effective_gateway_release_gateway_profile_id_fkey FOREIGN KEY (gateway_profile_id) REFERENCES public.gateway_profile(id) ON DELETE RESTRICT;


--
-- Name: effective_gateway_release effective_gateway_release_publication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.effective_gateway_release
    ADD CONSTRAINT effective_gateway_release_publication_id_fkey FOREIGN KEY (publication_id) REFERENCES public.gateway_publication(id) ON DELETE RESTRICT;


--
-- Name: gateway_publication_audit gateway_publication_audit_publication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication_audit
    ADD CONSTRAINT gateway_publication_audit_publication_id_fkey FOREIGN KEY (publication_id) REFERENCES public.gateway_publication(id) ON DELETE CASCADE;


--
-- Name: gateway_publication gateway_publication_base_release_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication
    ADD CONSTRAINT gateway_publication_base_release_id_fkey FOREIGN KEY (base_release_id) REFERENCES public.gateway_publication(id) ON DELETE RESTRICT;


--
-- Name: gateway_publication gateway_publication_gateway_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication
    ADD CONSTRAINT gateway_publication_gateway_profile_id_fkey FOREIGN KEY (gateway_profile_id) REFERENCES public.gateway_profile(id) ON DELETE RESTRICT;


--
-- Name: gateway_publication_outbox gateway_publication_outbox_publication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication_outbox
    ADD CONSTRAINT gateway_publication_outbox_publication_id_fkey FOREIGN KEY (publication_id) REFERENCES public.gateway_publication(id) ON DELETE CASCADE;


--
-- Name: gateway_publication_secret gateway_publication_secret_publication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gateway_publication_secret
    ADD CONSTRAINT gateway_publication_secret_publication_id_fkey FOREIGN KEY (publication_id) REFERENCES public.gateway_publication(id) ON DELETE CASCADE;


--
-- Name: managed_model_metadata managed_model_metadata_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model_metadata
    ADD CONSTRAINT managed_model_metadata_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.managed_model(id) ON DELETE CASCADE;


--
-- Name: managed_model_metadata managed_model_metadata_publication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model_metadata
    ADD CONSTRAINT managed_model_metadata_publication_id_fkey FOREIGN KEY (publication_id) REFERENCES public.gateway_publication(id) ON DELETE RESTRICT;


--
-- Name: managed_model managed_model_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model
    ADD CONSTRAINT managed_model_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.model_provider(id) ON DELETE RESTRICT;


--
-- Name: managed_model managed_model_runtime_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model
    ADD CONSTRAINT managed_model_runtime_id_fkey FOREIGN KEY (runtime_id) REFERENCES public.model_runtime(id) ON DELETE RESTRICT;


--
-- Name: managed_model managed_model_runtime_provider_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_model
    ADD CONSTRAINT managed_model_runtime_provider_fk FOREIGN KEY (runtime_id, provider_id) REFERENCES public.model_runtime(id, provider_id) ON DELETE RESTRICT;


--
-- Name: model_provider_metadata model_provider_metadata_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_provider_metadata
    ADD CONSTRAINT model_provider_metadata_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.model_provider(id) ON DELETE CASCADE;


--
-- Name: model_runtime model_runtime_gateway_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runtime
    ADD CONSTRAINT model_runtime_gateway_profile_id_fkey FOREIGN KEY (gateway_profile_id) REFERENCES public.gateway_profile(id) ON DELETE SET NULL;


--
-- Name: model_runtime_metadata model_runtime_metadata_runtime_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runtime_metadata
    ADD CONSTRAINT model_runtime_metadata_runtime_id_fkey FOREIGN KEY (runtime_id) REFERENCES public.model_runtime(id) ON DELETE CASCADE;


--
-- Name: model_runtime model_runtime_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runtime
    ADD CONSTRAINT model_runtime_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.model_provider(id) ON DELETE RESTRICT;


--
-- Name: pinned_chart pinned_chart_report_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pinned_chart
    ADD CONSTRAINT pinned_chart_report_fk FOREIGN KEY (report_id) REFERENCES public.pinned_report(id) ON DELETE CASCADE;


--
-- Name: pinned_report_access pinned_report_access_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pinned_report_access
    ADD CONSTRAINT pinned_report_access_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.pinned_report(id) ON DELETE CASCADE;


--
-- Name: user_model_access user_model_access_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_model_access
    ADD CONSTRAINT user_model_access_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.managed_model(id) ON DELETE CASCADE;


--
-- Name: user_model_access user_model_access_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_model_access
    ADD CONSTRAINT user_model_access_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_model_policy(user_id) ON DELETE CASCADE;


--
-- Name: user_session user_session_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_session
    ADD CONSTRAINT user_session_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.app_user(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

SET search_path = public, pg_catalog;



-- Integrated from historical migration 041_pinned_report_layout.up.sql.

-- Layout is a sidecar instead of a new pinned_report column because production and test
-- can temporarily run different strict Pydantic row models against the shared database.
-- Old deployments keep selecting pinned_report.* and never see this new shape.
CREATE TABLE pinned_report_layout (
    report_id UUID PRIMARY KEY REFERENCES pinned_report(id) ON DELETE CASCADE,
    layout JSONB NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pinned_report_layout_shape CHECK (
        jsonb_typeof(layout) = 'object'
        AND layout ? 'version'
        AND layout ->> 'version' = '1'
        AND layout ? 'spans'
        AND jsonb_typeof(layout -> 'spans') = 'object'
        AND layout ? 'row_heights'
        AND jsonb_typeof(layout -> 'row_heights') = 'object'
    )
);

-- Integrated from historical migration 042_remove_cloud_code_cli.up.sql.

DELETE FROM managed_model model
USING model_runtime runtime
WHERE model.runtime_id = runtime.id
  AND runtime.runtime_kind = 'cloud_code_cli';

DELETE FROM model_runtime
WHERE runtime_kind = 'cloud_code_cli';

DELETE FROM model_provider provider
WHERE provider.id = '20000000-0000-4000-8000-000000000002'
  AND provider.name = 'Anthropic'
  AND NOT EXISTS (
      SELECT 1
      FROM model_runtime runtime
      WHERE runtime.provider_id = provider.id
  );

ALTER TABLE model_runtime
    DROP CONSTRAINT IF EXISTS model_runtime_runtime_kind_check;

ALTER TABLE model_runtime
    ADD CONSTRAINT model_runtime_runtime_kind_check
    CHECK (runtime_kind IN ('copilot_cli', 'foundry', 'openai_compatible'));

COMMENT ON COLUMN model_runtime.runtime_kind IS
    'Executable connection kind. Local Cloud Code CLI is excluded because Turnstile cannot govern or observe it through APIM.';

-- Integrated from historical migration 044_enforce_foundry_project_connection_uniqueness.up.sql.

CREATE UNIQUE INDEX model_runtime_gateway_foundry_project_unique_idx
    ON model_runtime (
        gateway_profile_id,
        lower(rtrim(config->>'project_endpoint', '/'))
    )
    WHERE gateway_profile_id IS NOT NULL
      AND runtime_kind = 'foundry'
      AND NULLIF(rtrim(config->>'project_endpoint', '/'), '') IS NOT NULL;

COMMENT ON INDEX model_runtime_gateway_foundry_project_unique_idx IS
    'Prevents duplicate Microsoft Foundry Project connections on the same gateway.';

-- Integrated from historical migration 045_apim_cache_read_hourly.up.sql.

CREATE TABLE apim_cache_read_hourly (
    api_id TEXT NOT NULL,
    dimension_type TEXT NOT NULL CHECK (
        dimension_type IN (
            'global', 'organization', 'department', 'project',
            'agent', 'user', 'model', 'runtime'
        )
    ),
    dimension_value TEXT NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    cache_read_tokens BIGINT NOT NULL CHECK (cache_read_tokens >= 0),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (api_id, dimension_type, dimension_value, bucket_start),
    CHECK (
        date_bin(
            INTERVAL '1 hour',
            bucket_start,
            TIMESTAMPTZ '2000-01-01 00:00:00+00'
        ) = bucket_start
    )
);

COMMENT ON TABLE apim_cache_read_hourly IS
    'Low-cardinality APIM Prompt Cached Tokens aggregates keyed by API, one isolated business dimension, and UTC hour.';

COMMENT ON COLUMN apim_cache_read_hourly.cache_read_tokens IS
    'Measured cache-read tokens. Cache creation/write is not exposed by APIM and is excluded.';

-- Integrated from historical migration 046_gateway_route_reconcile.up.sql.

ALTER TABLE gateway_publication
    DROP CONSTRAINT gateway_publication_publication_kind_check;

ALTER TABLE gateway_publication
    ADD CONSTRAINT gateway_publication_publication_kind_check
    CHECK (publication_kind IN (
        'model_add', 'model_remove', 'credential_rotation', 'route_reconcile'
    ));

COMMENT ON COLUMN gateway_publication.publication_kind IS
    'Model additions and removals change the Registry after APIM promotion; credential rotations switch a managed Runtime key; route reconciliation republishes the same model set with updated routing infrastructure only.';

-- Integrated from historical migration 047_github_copilot_billing.up.sql.

CREATE TABLE copilot_connection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization TEXT NOT NULL UNIQUE
        CHECK (
            organization = lower(organization)
            AND organization ~ '^[a-z0-9]([a-z0-9-]{0,37}[a-z0-9])?$'
        ),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    credential_ciphertext BYTEA NOT NULL,
    credential_hint TEXT NOT NULL CHECK (length(trim(credential_hint)) > 0),
    write_enabled BOOLEAN NOT NULL DEFAULT false,
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL CHECK (length(trim(updated_by)) > 0)
);

CREATE UNIQUE INDEX copilot_connection_one_default_idx
    ON copilot_connection (is_default)
    WHERE is_default;

CREATE TABLE copilot_identity (
    app_user_id UUID PRIMARY KEY REFERENCES app_user (id) ON DELETE CASCADE,
    github_login TEXT NOT NULL UNIQUE
        CHECK (
            github_login = lower(github_login)
            AND github_login ~ '^[a-z0-9]([a-z0-9-]{0,37}[a-z0-9])?$'
        ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL CHECK (length(trim(updated_by)) > 0)
);

CREATE TABLE copilot_budget_request (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id UUID NOT NULL REFERENCES copilot_connection (id) ON DELETE RESTRICT,
    organization TEXT NOT NULL,
    app_user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE RESTRICT,
    user_email TEXT NOT NULL,
    user_display_name TEXT,
    github_login TEXT NOT NULL,
    requested_amount_usd INTEGER NOT NULL CHECK (requested_amount_usd > 0),
    approved_amount_usd INTEGER CHECK (approved_amount_usd > 0),
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    github_sync_status TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (github_sync_status IN (
            'not_requested', 'skipped', 'created', 'updated', 'failed'
        )),
    github_sync_error TEXT,
    reviewed_by TEXT,
    review_comment TEXT,
    reviewed_at TIMESTAMPTZ,
    processing_by TEXT,
    processing_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (status = 'pending' AND reviewed_at IS NULL)
        OR (status <> 'pending' AND reviewed_at IS NOT NULL)
    ),
    CHECK (
        (status = 'approved' AND approved_amount_usd IS NOT NULL)
        OR (status <> 'approved' AND approved_amount_usd IS NULL)
    )
);

CREATE UNIQUE INDEX copilot_budget_request_one_pending_idx
    ON copilot_budget_request (connection_id, app_user_id)
    WHERE status = 'pending';
CREATE INDEX copilot_budget_request_status_created_idx
    ON copilot_budget_request (status, created_at DESC);
CREATE INDEX copilot_budget_request_user_created_idx
    ON copilot_budget_request (app_user_id, created_at DESC);

CREATE TABLE copilot_budget_request_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES copilot_budget_request (id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('created', 'approved', 'rejected')),
    actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
    amount_usd INTEGER CHECK (amount_usd > 0),
    comment TEXT NOT NULL DEFAULT '',
    github_sync_status TEXT NOT NULL
        CHECK (github_sync_status IN (
            'not_requested', 'skipped', 'created', 'updated', 'failed'
        )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX copilot_budget_request_audit_request_created_idx
    ON copilot_budget_request_audit (request_id, created_at DESC);

COMMENT ON TABLE copilot_connection IS
    'Server-side GitHub Copilot organization billing connections. Tokens are Fernet ciphertext and never enter API responses.';
COMMENT ON TABLE copilot_identity IS
    'Explicit application-user to GitHub-login mapping. Email similarity is never treated as identity proof.';
COMMENT ON TABLE copilot_budget_request IS
    'Member-authored whole-dollar monthly AI-credit budget requests with Owner review and optional GitHub synchronization.';

-- Integrated from historical migration 048_apim_usage_domain_isolation.up.sql.

-- The APIM observability and Token budget surfaces are one data domain. GitHub Copilot
-- organization billing is served from /api/v1/copilot and must never be folded into these
-- legacy SQL functions merely because Copilot CLI telemetry shares token_usage storage.

CREATE OR REPLACE FUNCTION token_observability_metric(
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'et', COALESCE(SUM(et), 0)::DOUBLE PRECISION,
        'total_tokens', COALESCE(SUM(input_tokens + cached_tokens + output_tokens), 0)::BIGINT,
        'input_tokens', COALESCE(SUM(input_tokens), 0)::BIGINT,
        'cached_tokens', COALESCE(SUM(cached_tokens), 0)::BIGINT,
        'output_tokens', COALESCE(SUM(output_tokens), 0)::BIGINT,
        'calls', COUNT(*)::BIGINT
    )
    FROM token_usage
    WHERE ts >= p_from AND ts < p_to
      AND ingest_source <> 'copilot_cli';
$$;

CREATE OR REPLACE FUNCTION token_observability_workflow_metric(
    p_workflow TEXT,
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'et', COALESCE(SUM(et), 0)::DOUBLE PRECISION,
        'total_tokens', COALESCE(SUM(input_tokens + cached_tokens + output_tokens), 0)::BIGINT,
        'input_tokens', COALESCE(SUM(input_tokens), 0)::BIGINT,
        'cached_tokens', COALESCE(SUM(cached_tokens), 0)::BIGINT,
        'output_tokens', COALESCE(SUM(output_tokens), 0)::BIGINT,
        'calls', COUNT(*)::BIGINT
    )
    FROM token_usage
    WHERE workflow = p_workflow AND ts >= p_from AND ts < p_to
      AND ingest_source <> 'copilot_cli';
$$;

CREATE OR REPLACE FUNCTION token_observability_overview(
    p_timezone TEXT
) RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_now TIMESTAMPTZ := now();
    v_today_start TIMESTAMPTZ;
    v_month_start TIMESTAMPTZ;
    v_today JSONB;
    v_month JSONB;
    v_previous_today JSONB;
    v_previous_month JSONB;
    v_top JSONB;
    v_month_et NUMERIC;
    v_unattributed DOUBLE PRECISION;
    v_estimated DOUBLE PRECISION;
BEGIN
    v_today_start := date_trunc('day', v_now AT TIME ZONE p_timezone) AT TIME ZONE p_timezone;
    v_month_start := date_trunc('month', v_now AT TIME ZONE p_timezone) AT TIME ZONE p_timezone;
    v_today := token_observability_metric(v_today_start, v_now);
    v_month := token_observability_metric(v_month_start, v_now);
    v_previous_today := token_observability_metric(
        v_today_start - (v_now - v_today_start), v_today_start
    );
    v_previous_month := token_observability_metric(
        v_month_start - (v_now - v_month_start), v_month_start
    );
    v_month_et := (v_month->>'et')::NUMERIC;

    SELECT COALESCE(jsonb_agg(item ORDER BY et_sort DESC), '[]'::jsonb)
    INTO v_top
    FROM (
        SELECT jsonb_build_object(
            'workflow', workflow,
            'totals', jsonb_build_object(
                'et', SUM(et)::DOUBLE PRECISION,
                'total_tokens', SUM(input_tokens + cached_tokens + output_tokens)::BIGINT,
                'input_tokens', SUM(input_tokens)::BIGINT,
                'cached_tokens', SUM(cached_tokens)::BIGINT,
                'output_tokens', SUM(output_tokens)::BIGINT,
                'calls', COUNT(*)::BIGINT
            ),
            'share_percent', CASE WHEN v_month_et = 0 THEN 0
                ELSE ROUND((SUM(et) / v_month_et) * 100, 2)::DOUBLE PRECISION END,
            'et_sort', SUM(et)
        ) - 'et_sort' AS item,
        SUM(et) AS et_sort
        FROM token_usage
        WHERE ts >= v_month_start AND ts < v_now
          AND ingest_source <> 'copilot_cli'
        GROUP BY workflow
        ORDER BY SUM(et) DESC
        LIMIT 10
    ) ranked;

    SELECT
        COALESCE(100.0 * COUNT(*) FILTER (
            WHERE team = 'unattributed' OR user_ref = 'unattributed'
               OR agent = 'unattributed' OR workflow = 'unattributed'
        ) / NULLIF(COUNT(*), 0), 0),
        COALESCE(100.0 * COUNT(*) FILTER (WHERE estimated) / NULLIF(COUNT(*), 0), 0)
    INTO v_unattributed, v_estimated
    FROM token_usage
    WHERE ts >= v_month_start AND ts < v_now
      AND ingest_source <> 'copilot_cli';

    RETURN jsonb_build_object(
        'as_of', v_now,
        'timezone', p_timezone,
        'today', jsonb_build_object(
            'from', v_today_start,
            'to', v_now,
            'totals', v_today,
            'comparison_percent', token_observability_change(
                (v_today->>'et')::NUMERIC, (v_previous_today->>'et')::NUMERIC
            )
        ),
        'month', jsonb_build_object(
            'from', v_month_start,
            'to', v_now,
            'totals', v_month,
            'comparison_percent', token_observability_change(
                (v_month->>'et')::NUMERIC, (v_previous_month->>'et')::NUMERIC
            )
        ),
        'top_workflows', v_top,
        'unattributed_percent', ROUND(v_unattributed::NUMERIC, 2)::DOUBLE PRECISION,
        'estimated_percent', ROUND(v_estimated::NUMERIC, 2)::DOUBLE PRECISION
    );
END;
$$;

CREATE OR REPLACE FUNCTION token_observability_trends(
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ,
    p_interval TEXT,
    p_group_by TEXT,
    p_timezone TEXT
) RETURNS TABLE (
    bucket_start TIMESTAMPTZ,
    key TEXT,
    label TEXT,
    totals JSONB
)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_interval NOT IN ('hour', 'day', 'week') THEN
        RAISE EXCEPTION 'Unsupported interval: %', p_interval;
    END IF;
    IF p_group_by NOT IN ('team', 'agent', 'workflow', 'model') THEN
        RAISE EXCEPTION 'Unsupported grouping: %', p_group_by;
    END IF;

    RETURN QUERY
    SELECT
        date_trunc(p_interval, usage.ts AT TIME ZONE p_timezone) AT TIME ZONE p_timezone,
        CASE p_group_by
            WHEN 'team' THEN usage.team
            WHEN 'agent' THEN usage.agent
            WHEN 'workflow' THEN usage.workflow
            ELSE usage.model
        END,
        CASE p_group_by
            WHEN 'team' THEN usage.team
            WHEN 'agent' THEN usage.agent
            WHEN 'workflow' THEN usage.workflow
            ELSE usage.model
        END,
        jsonb_build_object(
            'et', SUM(usage.et)::DOUBLE PRECISION,
            'total_tokens', SUM(usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT,
            'input_tokens', SUM(usage.input_tokens)::BIGINT,
            'cached_tokens', SUM(usage.cached_tokens)::BIGINT,
            'output_tokens', SUM(usage.output_tokens)::BIGINT,
            'calls', COUNT(*)::BIGINT
        )
    FROM token_usage usage
    WHERE usage.ts >= p_from AND usage.ts < p_to
      AND usage.ingest_source <> 'copilot_cli'
    GROUP BY 1, 2, 3
    ORDER BY 1, 2;
END;
$$;

CREATE OR REPLACE FUNCTION token_observability_runs(
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ,
    p_limit INTEGER
) RETURNS TABLE (
    run_id TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    team TEXT,
    "user" TEXT,
    agent TEXT,
    workflow TEXT,
    provider TEXT,
    models TEXT[],
    turn_count INTEGER,
    totals JSONB,
    latency_ms BIGINT,
    estimated BOOLEAN
)
LANGUAGE sql STABLE AS $$
    SELECT
        usage.run_id,
        MIN(usage.ts),
        MAX(usage.ts),
        MIN(usage.team),
        MIN(usage.user_ref),
        MIN(usage.agent),
        MIN(usage.workflow),
        MIN(usage.provider),
        ARRAY_AGG(DISTINCT usage.model ORDER BY usage.model),
        COUNT(*)::INTEGER,
        jsonb_build_object(
            'et', SUM(usage.et)::DOUBLE PRECISION,
            'total_tokens', SUM(usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT,
            'input_tokens', SUM(usage.input_tokens)::BIGINT,
            'cached_tokens', SUM(usage.cached_tokens)::BIGINT,
            'output_tokens', SUM(usage.output_tokens)::BIGINT,
            'calls', COUNT(*)::BIGINT
        ),
        SUM(usage.latency_ms)::BIGINT,
        BOOL_OR(usage.estimated)
    FROM token_usage usage
    WHERE usage.ts >= p_from AND usage.ts < p_to
      AND usage.ingest_source <> 'copilot_cli'
    GROUP BY usage.run_id
    ORDER BY MAX(usage.ts) DESC
    LIMIT p_limit;
$$;

CREATE OR REPLACE FUNCTION token_observability_run_detail(
    p_run_id TEXT
) RETURNS JSONB
LANGUAGE sql STABLE AS $$
    WITH summary AS (
        SELECT
            run_id,
            MIN(ts) AS started_at,
            MAX(ts) AS ended_at,
            MIN(team) AS team,
            MIN(user_ref) AS user_ref,
            MIN(agent) AS agent,
            MIN(workflow) AS workflow,
            MIN(provider) AS provider,
            ARRAY_AGG(DISTINCT model ORDER BY model) AS models,
            COUNT(*)::INTEGER AS turn_count,
            jsonb_build_object(
                'et', SUM(et)::DOUBLE PRECISION,
                'total_tokens', SUM(input_tokens + cached_tokens + output_tokens)::BIGINT,
                'input_tokens', SUM(input_tokens)::BIGINT,
                'cached_tokens', SUM(cached_tokens)::BIGINT,
                'output_tokens', SUM(output_tokens)::BIGINT,
                'calls', COUNT(*)::BIGINT
            ) AS totals,
            SUM(latency_ms)::BIGINT AS latency_ms,
            BOOL_OR(estimated) AS estimated
        FROM token_usage
        WHERE run_id = p_run_id
          AND ingest_source <> 'copilot_cli'
        GROUP BY run_id
    ), turns AS (
        SELECT jsonb_agg(jsonb_build_object(
            'usage_id', id,
            'turn_index', turn_index,
            'ts', ts,
            'model', model,
            'input_tokens', input_tokens,
            'cached_tokens', cached_tokens,
            'output_tokens', output_tokens,
            'et', et::DOUBLE PRECISION,
            'et_coeff_m', et_coeff_m::DOUBLE PRECISION,
            'latency_ms', latency_ms,
            'status', status,
            'estimated', estimated,
            'ingest_source', ingest_source
        ) ORDER BY turn_index) AS items
        FROM token_usage
        WHERE run_id = p_run_id
          AND ingest_source <> 'copilot_cli'
    )
    SELECT jsonb_build_object(
        'run_id', summary.run_id,
        'started_at', summary.started_at,
        'ended_at', summary.ended_at,
        'team', summary.team,
        'user', summary.user_ref,
        'agent', summary.agent,
        'workflow', summary.workflow,
        'provider', summary.provider,
        'models', summary.models,
        'turn_count', summary.turn_count,
        'totals', summary.totals,
        'latency_ms', summary.latency_ms,
        'estimated', summary.estimated,
        'turns', turns.items
    )
    FROM summary CROSS JOIN turns;
$$;

COMMENT ON FUNCTION token_observability_overview(TEXT) IS
    'Legacy APIM overview. GitHub Copilot CLI telemetry is isolated from this data domain.';
COMMENT ON FUNCTION token_observability_runs(TIMESTAMPTZ, TIMESTAMPTZ, INTEGER) IS
    'Legacy APIM run list. GitHub Copilot CLI telemetry is isolated from this data domain.';

-- Integrated from historical migration 049_copilot_github_oauth.up.sql.

CREATE TABLE copilot_oauth_setting (
    origin TEXT PRIMARY KEY CHECK (origin ~ '^https?://[^/]+$'),
    client_id TEXT NOT NULL CHECK (length(trim(client_id)) > 0),
    client_secret_ciphertext BYTEA NOT NULL,
    client_secret_hint TEXT NOT NULL CHECK (length(trim(client_secret_hint)) > 0),
    callback_url TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL CHECK (length(trim(updated_by)) > 0)
);

CREATE TABLE copilot_oauth_state (
    state_sha256 TEXT PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    origin TEXT NOT NULL,
    return_path TEXT NOT NULL DEFAULT '/?page=finops-overview&source=github-copilot',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CHECK (expires_at > created_at),
    CHECK (return_path LIKE '/%' AND return_path NOT LIKE '//%')
);

CREATE INDEX copilot_oauth_state_expires_idx ON copilot_oauth_state (expires_at);

COMMENT ON TABLE copilot_oauth_setting IS
    'GitHub OAuth App identity for linking an existing Turnstile session to a GitHub login; the client secret is Fernet ciphertext.';
COMMENT ON TABLE copilot_oauth_state IS
    'Single-use, ten-minute OAuth CSRF states bound to the initiating Turnstile user.';

-- Integrated from historical migration 050_copilot_oauth_return_origin.up.sql.

ALTER TABLE copilot_oauth_state
    ADD COLUMN return_origin TEXT;

UPDATE copilot_oauth_state
   SET return_origin = origin
 WHERE return_origin IS NULL;

ALTER TABLE copilot_oauth_state
    ALTER COLUMN return_origin SET NOT NULL,
    ADD CONSTRAINT copilot_oauth_state_return_origin_check
        CHECK (return_origin ~ '^https?://[^/]+$');

COMMENT ON COLUMN copilot_oauth_state.return_origin IS
    'Validated browser origin to return to after a cross-domain GitHub callback.';

-- Integrated from historical migration 051_copilot_oauth_connection_purpose.up.sql.

ALTER TABLE copilot_oauth_state
    ADD COLUMN purpose TEXT NOT NULL DEFAULT 'identity',
    ADD COLUMN organization TEXT;

ALTER TABLE copilot_oauth_state
    ADD CONSTRAINT copilot_oauth_state_purpose_check
        CHECK (purpose IN ('identity', 'organization')),
    ADD CONSTRAINT copilot_oauth_state_organization_check
        CHECK (
            (purpose = 'identity' AND organization IS NULL)
            OR (
                purpose = 'organization'
                AND organization IS NOT NULL
                AND organization = lower(organization)
                AND organization ~ '^[a-z0-9]([a-z0-9-]{0,37}[a-z0-9])?$'
            )
        );

COMMENT ON COLUMN copilot_oauth_state.purpose IS
    'identity links one user; organization also authorizes and creates a data connection.';
COMMENT ON COLUMN copilot_oauth_state.organization IS
    'Requested organization for an Owner organization-connection authorization.';

-- Integrated from historical migration 052_copilot_enterprise_governance.up.sql.

CREATE TABLE copilot_usage_import (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id UUID NOT NULL REFERENCES copilot_connection(id) ON DELETE RESTRICT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('ai_usage', 'usage_report')),
    filename TEXT NOT NULL CHECK (length(trim(filename)) BETWEEN 1 AND 255),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes BETWEEN 1 AND 5242880),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 1 AND 25000),
    inserted_count INTEGER NOT NULL CHECK (inserted_count BETWEEN 0 AND row_count),
    duplicate_count INTEGER NOT NULL CHECK (duplicate_count BETWEEN 0 AND row_count),
    first_usage_date DATE NOT NULL,
    last_usage_date DATE NOT NULL,
    uploaded_by UUID NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
    uploaded_by_email TEXT NOT NULL CHECK (length(trim(uploaded_by_email)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (first_usage_date <= last_usage_date),
    CHECK (inserted_count + duplicate_count = row_count),
    UNIQUE (connection_id, source_kind, content_sha256)
);

CREATE TABLE copilot_usage_import_row (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_id UUID NOT NULL REFERENCES copilot_usage_import(id) ON DELETE RESTRICT,
    connection_id UUID NOT NULL REFERENCES copilot_connection(id) ON DELETE RESTRICT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('ai_usage', 'usage_report')),
    organization TEXT NOT NULL CHECK (organization = lower(organization)),
    usage_date DATE NOT NULL,
    username TEXT NOT NULL CHECK (username = lower(username)),
    model TEXT,
    product TEXT,
    sku TEXT,
    unit_type TEXT,
    cost_center_name TEXT,
    quantity NUMERIC(20, 4) NOT NULL CHECK (quantity >= 0),
    gross_amount NUMERIC(20, 4) NOT NULL CHECK (gross_amount >= 0),
    discount_amount NUMERIC(20, 4) NOT NULL CHECK (discount_amount >= 0),
    net_amount NUMERIC(20, 4) NOT NULL CHECK (net_amount >= 0),
    total_monthly_quota NUMERIC(20, 4) CHECK (total_monthly_quota >= 0),
    row_sha256 TEXT NOT NULL CHECK (row_sha256 ~ '^[0-9a-f]{64}$'),
    raw_row JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (source_kind = 'ai_usage' AND model IS NOT NULL)
        OR (
            source_kind = 'usage_report'
            AND product IS NOT NULL
            AND sku IS NOT NULL
            AND unit_type IS NOT NULL
        )
    ),
    UNIQUE (connection_id, source_kind, row_sha256)
);

CREATE INDEX copilot_usage_import_row_date_idx
    ON copilot_usage_import_row (connection_id, source_kind, usage_date DESC);
CREATE INDEX copilot_usage_import_row_user_idx
    ON copilot_usage_import_row (connection_id, username, usage_date DESC);

CREATE TABLE copilot_cost_center_request (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id UUID NOT NULL REFERENCES copilot_connection(id) ON DELETE RESTRICT,
    organization TEXT NOT NULL,
    app_user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
    user_email TEXT NOT NULL,
    user_display_name TEXT,
    github_login TEXT NOT NULL,
    cost_center_id TEXT NOT NULL CHECK (length(trim(cost_center_id)) > 0),
    cost_center_name TEXT NOT NULL CHECK (length(trim(cost_center_name)) > 0),
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    github_sync_status TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (github_sync_status IN (
            'not_requested', 'skipped', 'updated', 'failed'
        )),
    github_sync_error TEXT,
    reviewed_by TEXT,
    review_comment TEXT,
    reviewed_at TIMESTAMPTZ,
    processing_by TEXT,
    processing_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (status = 'pending' AND reviewed_at IS NULL)
        OR (status <> 'pending' AND reviewed_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX copilot_cost_center_request_one_pending_idx
    ON copilot_cost_center_request (connection_id, app_user_id)
    WHERE status = 'pending';
CREATE INDEX copilot_cost_center_request_status_created_idx
    ON copilot_cost_center_request (status, created_at DESC);

CREATE TABLE copilot_cost_center_request_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES copilot_cost_center_request(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('created', 'approved', 'rejected')),
    actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
    comment TEXT NOT NULL DEFAULT '',
    github_sync_status TEXT NOT NULL
        CHECK (github_sync_status IN (
            'not_requested', 'skipped', 'updated', 'failed'
        )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX copilot_cost_center_request_audit_request_created_idx
    ON copilot_cost_center_request_audit (request_id, created_at DESC);

COMMENT ON TABLE copilot_usage_import IS
    'Owner-uploaded GitHub CSV metadata. Files are parsed in memory and are never persisted to local disk.';
COMMENT ON TABLE copilot_usage_import_row IS
    'Normalized supplemental GitHub AI Usage and Usage Report rows with immutable provenance and row-level deduplication.';
COMMENT ON TABLE copilot_cost_center_request IS
    'Member-authored cost-center assignment requests with Owner review and optional GitHub synchronization.';

-- Integrated from historical migration 053_usage_domain_isolation.up.sql.

-- Every token row remains auditable in one ledger, but product surfaces must select an
-- explicit data domain. APIM must not learn every current or future external source name.
ALTER TABLE token_usage
    ADD COLUMN usage_domain TEXT NOT NULL DEFAULT 'apim'
    CHECK (usage_domain IN ('apim', 'github_copilot'));

UPDATE token_usage
SET usage_domain = 'github_copilot'
WHERE ingest_source = 'copilot_cli'
   OR request_source LIKE 'copilot-assistant%';

CREATE INDEX token_usage_domain_ts_idx ON token_usage (usage_domain, ts DESC);

COMMENT ON COLUMN token_usage.usage_domain IS
    'Product data domain that may read this row. Ingest source remains the transport stage.';

CREATE OR REPLACE FUNCTION token_observability_metric(
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'et', COALESCE(SUM(et), 0)::DOUBLE PRECISION,
        'total_tokens', COALESCE(SUM(input_tokens + cached_tokens + output_tokens), 0)::BIGINT,
        'input_tokens', COALESCE(SUM(input_tokens), 0)::BIGINT,
        'cached_tokens', COALESCE(SUM(cached_tokens), 0)::BIGINT,
        'output_tokens', COALESCE(SUM(output_tokens), 0)::BIGINT,
        'calls', COUNT(*)::BIGINT
    )
    FROM token_usage
    WHERE ts >= p_from AND ts < p_to
      AND usage_domain = 'apim';
$$;

CREATE OR REPLACE FUNCTION token_observability_workflow_metric(
    p_workflow TEXT,
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'et', COALESCE(SUM(et), 0)::DOUBLE PRECISION,
        'total_tokens', COALESCE(SUM(input_tokens + cached_tokens + output_tokens), 0)::BIGINT,
        'input_tokens', COALESCE(SUM(input_tokens), 0)::BIGINT,
        'cached_tokens', COALESCE(SUM(cached_tokens), 0)::BIGINT,
        'output_tokens', COALESCE(SUM(output_tokens), 0)::BIGINT,
        'calls', COUNT(*)::BIGINT
    )
    FROM token_usage
    WHERE workflow = p_workflow AND ts >= p_from AND ts < p_to
      AND usage_domain = 'apim';
$$;

CREATE OR REPLACE FUNCTION token_observability_overview(
    p_timezone TEXT
) RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_now TIMESTAMPTZ := now();
    v_today_start TIMESTAMPTZ;
    v_month_start TIMESTAMPTZ;
    v_today JSONB;
    v_month JSONB;
    v_previous_today JSONB;
    v_previous_month JSONB;
    v_top JSONB;
    v_month_et NUMERIC;
    v_unattributed DOUBLE PRECISION;
    v_estimated DOUBLE PRECISION;
BEGIN
    v_today_start := date_trunc('day', v_now AT TIME ZONE p_timezone) AT TIME ZONE p_timezone;
    v_month_start := date_trunc('month', v_now AT TIME ZONE p_timezone) AT TIME ZONE p_timezone;
    v_today := token_observability_metric(v_today_start, v_now);
    v_month := token_observability_metric(v_month_start, v_now);
    v_previous_today := token_observability_metric(
        v_today_start - (v_now - v_today_start), v_today_start
    );
    v_previous_month := token_observability_metric(
        v_month_start - (v_now - v_month_start), v_month_start
    );
    v_month_et := (v_month->>'et')::NUMERIC;

    SELECT COALESCE(jsonb_agg(item ORDER BY et_sort DESC), '[]'::jsonb)
    INTO v_top
    FROM (
        SELECT jsonb_build_object(
            'workflow', workflow,
            'totals', jsonb_build_object(
                'et', SUM(et)::DOUBLE PRECISION,
                'total_tokens', SUM(input_tokens + cached_tokens + output_tokens)::BIGINT,
                'input_tokens', SUM(input_tokens)::BIGINT,
                'cached_tokens', SUM(cached_tokens)::BIGINT,
                'output_tokens', SUM(output_tokens)::BIGINT,
                'calls', COUNT(*)::BIGINT
            ),
            'share_percent', CASE WHEN v_month_et = 0 THEN 0
                ELSE ROUND((SUM(et) / v_month_et) * 100, 2)::DOUBLE PRECISION END,
            'et_sort', SUM(et)
        ) - 'et_sort' AS item,
        SUM(et) AS et_sort
        FROM token_usage
        WHERE ts >= v_month_start AND ts < v_now
          AND usage_domain = 'apim'
        GROUP BY workflow
        ORDER BY SUM(et) DESC
        LIMIT 10
    ) ranked;

    SELECT
        COALESCE(100.0 * COUNT(*) FILTER (
            WHERE team = 'unattributed' OR user_ref = 'unattributed'
               OR agent = 'unattributed' OR workflow = 'unattributed'
        ) / NULLIF(COUNT(*), 0), 0),
        COALESCE(100.0 * COUNT(*) FILTER (WHERE estimated) / NULLIF(COUNT(*), 0), 0)
    INTO v_unattributed, v_estimated
    FROM token_usage
    WHERE ts >= v_month_start AND ts < v_now
      AND usage_domain = 'apim';

    RETURN jsonb_build_object(
        'as_of', v_now,
        'timezone', p_timezone,
        'today', jsonb_build_object(
            'from', v_today_start,
            'to', v_now,
            'totals', v_today,
            'comparison_percent', token_observability_change(
                (v_today->>'et')::NUMERIC, (v_previous_today->>'et')::NUMERIC
            )
        ),
        'month', jsonb_build_object(
            'from', v_month_start,
            'to', v_now,
            'totals', v_month,
            'comparison_percent', token_observability_change(
                (v_month->>'et')::NUMERIC, (v_previous_month->>'et')::NUMERIC
            )
        ),
        'top_workflows', v_top,
        'unattributed_percent', ROUND(v_unattributed::NUMERIC, 2)::DOUBLE PRECISION,
        'estimated_percent', ROUND(v_estimated::NUMERIC, 2)::DOUBLE PRECISION
    );
END;
$$;

CREATE OR REPLACE FUNCTION token_observability_trends(
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ,
    p_interval TEXT,
    p_group_by TEXT,
    p_timezone TEXT
) RETURNS TABLE (
    bucket_start TIMESTAMPTZ,
    key TEXT,
    label TEXT,
    totals JSONB
)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_interval NOT IN ('hour', 'day', 'week') THEN
        RAISE EXCEPTION 'Unsupported interval: %', p_interval;
    END IF;
    IF p_group_by NOT IN ('team', 'agent', 'workflow', 'model') THEN
        RAISE EXCEPTION 'Unsupported grouping: %', p_group_by;
    END IF;

    RETURN QUERY
    SELECT
        date_trunc(p_interval, usage.ts AT TIME ZONE p_timezone) AT TIME ZONE p_timezone,
        CASE p_group_by
            WHEN 'team' THEN usage.team
            WHEN 'agent' THEN usage.agent
            WHEN 'workflow' THEN usage.workflow
            ELSE usage.model
        END,
        CASE p_group_by
            WHEN 'team' THEN usage.team
            WHEN 'agent' THEN usage.agent
            WHEN 'workflow' THEN usage.workflow
            ELSE usage.model
        END,
        jsonb_build_object(
            'et', SUM(usage.et)::DOUBLE PRECISION,
            'total_tokens', SUM(usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT,
            'input_tokens', SUM(usage.input_tokens)::BIGINT,
            'cached_tokens', SUM(usage.cached_tokens)::BIGINT,
            'output_tokens', SUM(usage.output_tokens)::BIGINT,
            'calls', COUNT(*)::BIGINT
        )
    FROM token_usage usage
    WHERE usage.ts >= p_from AND usage.ts < p_to
      AND usage.usage_domain = 'apim'
    GROUP BY 1, 2, 3
    ORDER BY 1, 2;
END;
$$;

CREATE OR REPLACE FUNCTION token_observability_runs(
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ,
    p_limit INTEGER
) RETURNS TABLE (
    run_id TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    team TEXT,
    "user" TEXT,
    agent TEXT,
    workflow TEXT,
    provider TEXT,
    models TEXT[],
    turn_count INTEGER,
    totals JSONB,
    latency_ms BIGINT,
    estimated BOOLEAN
)
LANGUAGE sql STABLE AS $$
    SELECT
        usage.run_id,
        MIN(usage.ts),
        MAX(usage.ts),
        MIN(usage.team),
        MIN(usage.user_ref),
        MIN(usage.agent),
        MIN(usage.workflow),
        MIN(usage.provider),
        ARRAY_AGG(DISTINCT usage.model ORDER BY usage.model),
        COUNT(*)::INTEGER,
        jsonb_build_object(
            'et', SUM(usage.et)::DOUBLE PRECISION,
            'total_tokens', SUM(usage.input_tokens + usage.cached_tokens + usage.output_tokens)::BIGINT,
            'input_tokens', SUM(usage.input_tokens)::BIGINT,
            'cached_tokens', SUM(usage.cached_tokens)::BIGINT,
            'output_tokens', SUM(usage.output_tokens)::BIGINT,
            'calls', COUNT(*)::BIGINT
        ),
        SUM(usage.latency_ms)::BIGINT,
        BOOL_OR(usage.estimated)
    FROM token_usage usage
    WHERE usage.ts >= p_from AND usage.ts < p_to
      AND usage.usage_domain = 'apim'
    GROUP BY usage.run_id
    ORDER BY MAX(usage.ts) DESC
    LIMIT p_limit;
$$;

CREATE OR REPLACE FUNCTION token_observability_run_detail(
    p_run_id TEXT
) RETURNS JSONB
LANGUAGE sql STABLE AS $$
    WITH summary AS (
        SELECT
            run_id,
            MIN(ts) AS started_at,
            MAX(ts) AS ended_at,
            MIN(team) AS team,
            MIN(user_ref) AS user_ref,
            MIN(agent) AS agent,
            MIN(workflow) AS workflow,
            MIN(provider) AS provider,
            ARRAY_AGG(DISTINCT model ORDER BY model) AS models,
            COUNT(*)::INTEGER AS turn_count,
            jsonb_build_object(
                'et', SUM(et)::DOUBLE PRECISION,
                'total_tokens', SUM(input_tokens + cached_tokens + output_tokens)::BIGINT,
                'input_tokens', SUM(input_tokens)::BIGINT,
                'cached_tokens', SUM(cached_tokens)::BIGINT,
                'output_tokens', SUM(output_tokens)::BIGINT,
                'calls', COUNT(*)::BIGINT
            ) AS totals,
            SUM(latency_ms)::BIGINT AS latency_ms,
            BOOL_OR(estimated) AS estimated
        FROM token_usage
        WHERE run_id = p_run_id
          AND usage_domain = 'apim'
        GROUP BY run_id
    ), turns AS (
        SELECT jsonb_agg(jsonb_build_object(
            'usage_id', id,
            'turn_index', turn_index,
            'ts', ts,
            'model', model,
            'input_tokens', input_tokens,
            'cached_tokens', cached_tokens,
            'output_tokens', output_tokens,
            'et', et::DOUBLE PRECISION,
            'et_coeff_m', et_coeff_m::DOUBLE PRECISION,
            'latency_ms', latency_ms,
            'status', status,
            'estimated', estimated,
            'ingest_source', ingest_source
        ) ORDER BY turn_index) AS items
        FROM token_usage
        WHERE run_id = p_run_id
          AND usage_domain = 'apim'
    )
    SELECT jsonb_build_object(
        'run_id', summary.run_id,
        'started_at', summary.started_at,
        'ended_at', summary.ended_at,
        'team', summary.team,
        'user', summary.user_ref,
        'agent', summary.agent,
        'workflow', summary.workflow,
        'provider', summary.provider,
        'models', summary.models,
        'turn_count', summary.turn_count,
        'totals', summary.totals,
        'latency_ms', summary.latency_ms,
        'estimated', summary.estimated,
        'turns', turns.items
    )
    FROM summary CROSS JOIN turns;
$$;

COMMENT ON FUNCTION token_observability_overview(TEXT) IS
    'APIM overview. Only rows explicitly assigned to the APIM usage domain are included.';
COMMENT ON FUNCTION token_observability_runs(TIMESTAMPTZ, TIMESTAMPTZ, INTEGER) IS
    'APIM run list. Only rows explicitly assigned to the APIM usage domain are included.';

-- Integrated from historical migration 059_gateway_release_operations.up.sql.

CREATE TABLE gateway_release_protection (
    publication_id UUID PRIMARY KEY REFERENCES gateway_publication(id) ON DELETE RESTRICT,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    protected_label TEXT,
    retain_until TIMESTAMPTZ,
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (pinned OR protected_label IS NOT NULL OR retain_until IS NOT NULL)
);

CREATE TABLE gateway_release_protection_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publication_id UUID NOT NULL REFERENCES gateway_publication(id) ON DELETE RESTRICT,
    pinned BOOLEAN NOT NULL,
    protected_label TEXT,
    retain_until TIMESTAMPTZ,
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX gateway_release_protection_audit_release_idx
    ON gateway_release_protection_audit (publication_id, created_at, id);

CREATE TABLE gateway_release_operation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gateway_profile_id UUID NOT NULL REFERENCES gateway_profile(id) ON DELETE RESTRICT,
    operation_kind TEXT NOT NULL CHECK (operation_kind IN ('rollback', 'integrity_check', 'gc_plan')),
    target_release_id UUID REFERENCES gateway_publication(id) ON DELETE RESTRICT,
    prior_release_id UUID REFERENCES gateway_publication(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'validating_dependencies', 'preflight_probing', 'promoting',
        'verifying_readback', 'post_promotion_probing', 'restoring',
        'succeeded', 'failed', 'restored'
    )),
    confirmation_sha256 TEXT CHECK (
        confirmation_sha256 IS NULL OR confirmation_sha256 ~ '^[0-9a-f]{64}$'
    ),
    semantic_preview JSONB NOT NULL DEFAULT '{}'::jsonb,
    checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX gateway_release_operation_one_open_idx
    ON gateway_release_operation (gateway_profile_id)
    WHERE status NOT IN ('succeeded', 'failed', 'restored');

CREATE INDEX gateway_release_operation_target_idx
    ON gateway_release_operation (target_release_id, created_at DESC);

CREATE TABLE gateway_release_operation_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES gateway_release_operation(id) ON DELETE RESTRICT,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX gateway_release_operation_audit_operation_idx
    ON gateway_release_operation_audit (operation_id, created_at, id);

CREATE TABLE gateway_release_integrity_snapshot (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publication_id UUID NOT NULL REFERENCES gateway_publication(id) ON DELETE RESTRICT,
    operation_id UUID REFERENCES gateway_release_operation(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('healthy', 'missing', 'mismatched')),
    dependencies JSONB NOT NULL,
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    checked_by TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX gateway_release_integrity_snapshot_release_idx
    ON gateway_release_integrity_snapshot (publication_id, checked_at DESC);

CREATE TABLE gateway_release_gc_plan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL UNIQUE REFERENCES gateway_release_operation(id) ON DELETE RESTRICT,
    gateway_profile_id UUID NOT NULL REFERENCES gateway_profile(id) ON DELETE RESTRICT,
    retained_release_ids JSONB NOT NULL,
    current_non_release_references JSONB NOT NULL,
    candidates JSONB NOT NULL,
    reference_graph_sha256 TEXT NOT NULL CHECK (reference_graph_sha256 ~ '^[0-9a-f]{64}$'),
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Integrated from historical migration 060_gateway_application_access.up.sql.

CREATE TABLE gateway_application (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gateway_profile_id UUID NOT NULL REFERENCES gateway_profile(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,126}$'),
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 160),
    description TEXT,
    owner_id TEXT,
    department_id TEXT,
    application_type TEXT NOT NULL CHECK (
        application_type IN ('service', 'delegated_user', 'system')
    ),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'suspended', 'retired')
    ),
    system_managed BOOLEAN NOT NULL DEFAULT FALSE,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (gateway_profile_id, slug),
    UNIQUE (id, gateway_profile_id)
);

CREATE INDEX gateway_application_status_idx
    ON gateway_application (gateway_profile_id, status, display_name);

CREATE TABLE gateway_application_subscription (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id UUID NOT NULL,
    gateway_profile_id UUID NOT NULL REFERENCES gateway_profile(id) ON DELETE RESTRICT,
    apim_subscription_id TEXT NOT NULL CHECK (
        apim_subscription_id ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,126}$'
    ),
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 160),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('product', 'api', 'service')),
    scope_id TEXT NOT NULL CHECK (length(btrim(scope_id)) BETWEEN 1 AND 255),
    state TEXT NOT NULL CHECK (state IN ('active', 'suspended', 'cancelled')),
    scope_exists BOOLEAN NOT NULL DEFAULT TRUE,
    source TEXT NOT NULL DEFAULT 'discovered' CHECK (
        source IN ('bicep', 'discovered', 'managed')
    ),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (application_id, gateway_profile_id)
        REFERENCES gateway_application(id, gateway_profile_id) ON DELETE RESTRICT,
    UNIQUE (gateway_profile_id, apim_subscription_id)
);

CREATE INDEX gateway_application_subscription_application_idx
    ON gateway_application_subscription (application_id, state, apim_subscription_id);

CREATE TABLE gateway_application_budget (
    period_start DATE NOT NULL CHECK (period_start = date_trunc('month', period_start)::date),
    application_id UUID NOT NULL REFERENCES gateway_application(id) ON DELETE RESTRICT,
    token_limit BIGINT NOT NULL CHECK (token_limit > 0),
    tokens_per_minute BIGINT NOT NULL CHECK (tokens_per_minute > 0),
    enforce BOOLEAN NOT NULL DEFAULT TRUE,
    warning_threshold_percent INTEGER NOT NULL DEFAULT 80 CHECK (
        warning_threshold_percent BETWEEN 1 AND 100
    ),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (period_start, application_id)
);

CREATE TABLE gateway_application_model_policy (
    application_id UUID PRIMARY KEY REFERENCES gateway_application(id) ON DELETE RESTRICT,
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE gateway_application_model_access (
    application_id UUID NOT NULL REFERENCES gateway_application(id) ON DELETE RESTRICT,
    model_id UUID NOT NULL REFERENCES managed_model(id) ON DELETE RESTRICT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (application_id, model_id)
);

CREATE INDEX gateway_application_model_access_model_idx
    ON gateway_application_model_access (model_id, application_id);

CREATE TABLE gateway_application_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id UUID NOT NULL REFERENCES gateway_application(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL CHECK (operation IN (
        'created', 'adopted', 'updated', 'budget_updated', 'models_updated',
        'subscription_synced', 'suspended', 'resumed', 'retired'
    )),
    before_state JSONB,
    after_state JSONB,
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX gateway_application_audit_application_idx
    ON gateway_application_audit (application_id, created_at DESC, id);

CREATE TABLE token_usage_application_attribution (
    usage_id TEXT PRIMARY KEY REFERENCES token_usage(id) ON DELETE RESTRICT,
    application_id UUID NOT NULL REFERENCES gateway_application(id) ON DELETE RESTRICT,
    application_subscription_id UUID NOT NULL
        REFERENCES gateway_application_subscription(id) ON DELETE RESTRICT,
    application_name_snapshot TEXT NOT NULL CHECK (
        length(btrim(application_name_snapshot)) BETWEEN 1 AND 160
    ),
    apim_subscription_id TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('person', 'service', 'system')),
    actor_id TEXT NOT NULL CHECK (length(btrim(actor_id)) BETWEEN 1 AND 255),
    person_id TEXT,
    application_admission TEXT CHECK (
        application_admission IS NULL OR length(application_admission) BETWEEN 1 AND 64
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (actor_type = 'person' AND person_id IS NOT NULL)
        OR (actor_type <> 'person' AND person_id IS NULL)
    )
);

CREATE INDEX token_usage_application_attribution_application_idx
    ON token_usage_application_attribution (application_id, usage_id);

CREATE INDEX token_usage_application_attribution_subscription_idx
    ON token_usage_application_attribution (application_subscription_id, usage_id);

ALTER TABLE gateway_release_operation
    DROP CONSTRAINT gateway_release_operation_operation_kind_check;

ALTER TABLE gateway_release_operation
    ADD CONSTRAINT gateway_release_operation_operation_kind_check CHECK (
        operation_kind IN ('rollback', 'integrity_check', 'gc_plan', 'application_sync')
    );

-- Integrated from historical migration 061_owner_application_subscription_provision.up.sql.

ALTER TABLE gateway_application
    DROP CONSTRAINT gateway_application_application_type_check;

ALTER TABLE gateway_application
    ADD CONSTRAINT gateway_application_application_type_check CHECK (
        application_type IN ('service', 'agent', 'delegated_user', 'system')
    );

ALTER TABLE gateway_release_operation
    DROP CONSTRAINT gateway_release_operation_operation_kind_check;

ALTER TABLE gateway_release_operation
    ADD CONSTRAINT gateway_release_operation_operation_kind_check CHECK (
        operation_kind IN (
            'rollback', 'integrity_check', 'gc_plan', 'application_sync',
            'application_provision'
        )
    );

CREATE TABLE gateway_release_operation_secret (
    operation_id UUID PRIMARY KEY
        REFERENCES gateway_release_operation(id) ON DELETE CASCADE,
    credential_ciphertext BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE gateway_application_avatar (
    application_id UUID PRIMARY KEY
        REFERENCES gateway_application(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL CHECK (
        media_type IN ('image/png', 'image/jpeg', 'image/webp')
    ),
    image_bytes BYTEA NOT NULL CHECK (
        octet_length(image_bytes) BETWEEN 1 AND 65536
    ),
    updated_by TEXT NOT NULL CHECK (length(trim(updated_by)) > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Platform-owned APIM gateway. Connections, runtimes, and models are added after deployment.
INSERT INTO public.gateway_profile (
    id, name, implementation, base_url, auth_type, credential_ciphertext,
    credential_hint, enabled, is_default, config
) VALUES (
    '10000000-0000-4000-8000-000000000001',
    'Azure API Management',
    'apim',
    NULL,
    'api_key',
    NULL,
    NULL,
    TRUE,
    TRUE,
    '{"header_name": "Ocp-Apim-Subscription-Key"}'::jsonb
);
