ALTER TABLE report_attachments ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'upload';
ALTER TABLE report_attachments ADD COLUMN IF NOT EXISTS message_id uuid REFERENCES messages(id) ON DELETE SET NULL;
