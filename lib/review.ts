type OptionValue = string | { id: string } | { value: string };
export type ReviewValue = OptionValue | number | OptionValue[] | null;
export type ReviewField = {
  id: string;
  label: string;
  required: boolean;
  kind: 'text' | 'textarea' | 'select' | 'multiselect' | 'number' | 'date';
  options: { value: string; label: string }[];
  readOnly?: boolean;
};
export type TicketReviewData = {
  reportId: string;
  version: number;
  state: 'awaiting_approval' | 'approved';
  form: {
    requestTypeId: string;
    requestTypeName: string;
    fields: ReviewField[];
    values: Record<string, ReviewValue>;
    attachmentsAllowed: boolean;
    attachmentsRequired?: boolean;
    unsupportedFields: string[];
  };
  team: string;
  priority: string;
  verification: {
    status: 'passed' | 'needs_changes' | 'unavailable';
    issues: { field: string; message: string }[];
    checks: string[];
    model: string;
    promptVersion: string;
  };
  attachments: {
    id: string;
    filename: string;
    providerFilename: string;
    size: number;
    contentType: string;
    state:
      | 'staged'
      | 'pending'
      | 'succeeded'
      | 'unknown'
      | 'failed'
      | 'expired';
    error?: string;
  }[];
  approvedAt?: string;
};
