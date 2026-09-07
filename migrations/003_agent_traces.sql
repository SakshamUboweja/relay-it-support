CREATE TABLE IF NOT EXISTS agent_runs(
 id uuid PRIMARY KEY,
 report_id uuid NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
 decision_id uuid REFERENCES decisions(id) ON DELETE SET NULL,
 pipeline text NOT NULL CHECK(pipeline IN ('deterministic','single','multi')),
 scoring text NOT NULL, model text, reasoning_effort text,
 status text NOT NULL CHECK(status IN ('completed','failed','budget_exhausted','skipped')),
 budget jsonb NOT NULL, usage jsonb NOT NULL, cost_usd numeric(12,6), pricing_version text,
 latency_ms integer NOT NULL, outcome jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS agent_runs_report ON agent_runs(report_id);
CREATE TABLE IF NOT EXISTS agent_steps(
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
 seq integer NOT NULL, role text NOT NULL, kind text NOT NULL CHECK(kind IN ('model_call','tool_call','policy')),
 model text, prompt_version text, input_summary text NOT NULL, output_summary text NOT NULL,
 tool_name text, tool_args jsonb, tool_result_summary text,
 usage jsonb NOT NULL DEFAULT '{"input":0,"output":0,"cached":0,"reasoning":0}',
 cost_usd numeric(12,6), latency_ms integer NOT NULL,
 status text NOT NULL CHECK(status IN ('ok','error','timeout','rejected','skipped')), error text,
 detail jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run_id,seq));
