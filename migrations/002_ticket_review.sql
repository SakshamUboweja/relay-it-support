ALTER TABLE reports ADD COLUMN IF NOT EXISTS requires_approval boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS ticket_reviews (
 report_id uuid PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
 version integer NOT NULL DEFAULT 1,
 content jsonb NOT NULL,
 approved_at timestamptz,
 approved_by text REFERENCES users(id),
 approved_payload jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS report_attachments (
 id uuid PRIMARY KEY,
 report_id uuid NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
 filename text NOT NULL,
 provider_filename text NOT NULL,
 content_type text NOT NULL,
 size integer NOT NULL CHECK(size>0 AND size<=5242880),
 content bytea,
 state text NOT NULL DEFAULT 'staged',
 attempts integer NOT NULL DEFAULT 0,
 last_error text,
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS attachment_report ON report_attachments(report_id);

-- Enforce approval even if an older worker overlaps a rolling deployment.
CREATE OR REPLACE FUNCTION enforce_ticket_approval() RETURNS trigger AS $$
BEGIN
 IF NEW.kind='create' AND EXISTS(SELECT 1 FROM reports WHERE id=NEW.report_id AND requires_approval)
 AND NOT EXISTS(
  SELECT 1 FROM ticket_reviews t JOIN reports r ON r.id=t.report_id
  WHERE t.report_id=NEW.report_id AND t.approved_at IS NOT NULL AND t.approved_by=r.owner_id
    AND t.version::text=NEW.payload->>'approvalVersion'
    AND t.approved_payload=NEW.payload->'approvedPayload'
 ) THEN
  RAISE EXCEPTION 'Requester approval is required before queuing a Jira ticket' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE TRIGGER require_ticket_approval
 BEFORE INSERT OR UPDATE OF payload ON connector_operations
 FOR EACH ROW EXECUTE FUNCTION enforce_ticket_approval();
