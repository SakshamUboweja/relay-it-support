'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Paperclip,
  RefreshCw,
  ShieldCheck,
  Trash2,
  ChevronRight,
  ExternalLink,
  FileText,
  Headphones,
  LockKeyhole,
  Upload,
  UserRound,
} from 'lucide-react';
import type { ReviewField, ReviewValue, TicketReviewData } from '@/lib/review';

class ReviewError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ReviewError(
      data.error || 'The ticket could not be updated. Please retry.',
      response.status,
    );
  return data;
}
const empty = (value: ReviewValue | undefined) =>
  value == null ||
  (typeof value === 'string' && !value.trim()) ||
  (Array.isArray(value) && !value.length);
const displayValue = (value: ReviewValue | undefined): string => {
  if (value == null) return '';
  if (typeof value === 'object') {
    if ('id' in value) return value.id;
    if ('value' in value) return value.value;
    return JSON.stringify(value);
  }
  return String(value);
};
const attachmentLabels = {
  staged: 'Ready to send after approval',
  pending: 'Sending to Jira',
  succeeded: 'Attached in Jira',
  unknown: 'Attachment confirmation pending',
  failed: 'Attachment failed',
  expired: 'File expired — remove it and attach it again',
};

export function TicketReview({
  reportId,
  reportState,
  mode,
  requesterName,
  providerKey,
  providerUrl,
  providerStatus,
  onApproved,
}: {
  reportId: string;
  reportState: string;
  mode: string;
  requesterName: string;
  providerKey: string | null;
  providerUrl: string | null;
  providerStatus: string | null;
  onApproved: () => Promise<unknown>;
}) {
  const [review, setReview] = useState<TicketReviewData | null>(null);
  const [values, setValues] = useState<Record<string, ReviewValue>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [dragging, setDragging] = useState(false);
  const latest = useRef({ dirty: false, busy: '', version: 0 });
  const generation = useRef(0);
  const alive = useRef(true);
  const fileInput = useRef<HTMLInputElement>(null);

  const adopt = useCallback((next: TicketReviewData) => {
    setReview(next);
    setValues(next.form.values);
    setDirty(false);
    setConflict(false);
    latest.current.dirty = false;
    latest.current.version = next.version;
  }, []);

  const load = useCallback(
    async (replace = false) => {
      if (!replace && latest.current.busy) return;
      const started = generation.current;
      try {
        const data = await request<{ review: TicketReviewData }>(
          '/api/review?id=' + encodeURIComponent(reportId),
        );
        if (!alive.current || started !== generation.current) return;
        if (!replace && latest.current.dirty) {
          if (
            data.review.version !== latest.current.version ||
            data.review.state === 'approved'
          )
            setConflict(true);
          return;
        }
        adopt(data.review);
        if (replace) {
          setError('');
          setNotice('Latest ticket loaded.');
        }
      } catch (e) {
        if (!alive.current || started !== generation.current) return;
        if (!(e instanceof ReviewError && e.status === 404))
          setError((e as Error).message);
      } finally {
        if (alive.current) setLoading(false);
      }
    },
    [adopt, reportId],
  );

  useEffect(() => {
    alive.current = true;
    generation.current++;
    const initial = setTimeout(() => void load(), 0);
    const timer = setInterval(() => void load(), 4000);
    return () => {
      alive.current = false;
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, [load]);

  function edit(id: string, value: ReviewValue) {
    latest.current.dirty = true;
    setDirty(true);
    setNotice('');
    setValues((current) => ({ ...current, [id]: value }));
  }
  async function mutate(
    label: string,
    url: string,
    init: RequestInit,
    approving = false,
  ) {
    if (latest.current.busy) return;
    generation.current++;
    latest.current.busy = label;
    setBusy(label);
    setError('');
    setNotice('');
    try {
      const data = await request<{ review?: TicketReviewData }>(url, init);
      if (!alive.current) return;
      if (data.review) adopt(data.review);
      if (approving) {
        setReview((current) =>
          current ? { ...current, state: 'approved' } : current,
        );
        await load(true);
        setNotice('Approval saved. You can follow delivery above.');
        await onApproved();
      } else
        setNotice(
          label === 'Verifying'
            ? 'Saved and checked by the verifier.'
            : 'Attachment list updated.',
        );
    } catch (e) {
      if (!alive.current) return;
      if (e instanceof ReviewError && e.status === 409) {
        setConflict(true);
        setError(
          'This ticket changed since you opened it. Your edits are still here. Load the latest version before making further changes.',
        );
      } else setError((e as Error).message);
    } finally {
      latest.current.busy = '';
      if (alive.current) setBusy('');
    }
  }

  const json = (body: unknown): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const approved = review?.state === 'approved';
  const missing =
    review?.form.fields.filter(
      (field) => field.required && empty(values[field.id]),
    ) ?? [];
  const blockingUnsupported =
    review?.form.fields.filter(
      (field) =>
        review.form.unsupportedFields.includes(field.id) &&
        (field.required || !empty(values[field.id])),
    ) ?? [];
  const canApprove =
    !!review &&
    !approved &&
    review.verification.status === 'passed' &&
    !missing.length &&
    !blockingUnsupported.length &&
    review.attachments.every((file) => file.state === 'staged') &&
    (!review.form.attachmentsRequired || review.attachments.length > 0) &&
    !busy &&
    !dirty &&
    !conflict;

  async function attach(file: File) {
    if (
      !review ||
      dirty ||
      conflict ||
      approved ||
      latest.current.busy ||
      !review.form.attachmentsAllowed
    )
      return;
    if (file.size > 5 * 1024 * 1024 || file.size === 0) {
      setError('Choose a nonempty file up to 5 MB.');
      return;
    }
    if (!/\.(pdf|png|jpe?g|txt|log)$/i.test(file.name)) {
      setError('Choose a PDF, PNG, JPEG, TXT, or LOG file.');
      return;
    }
    if (review.attachments.length >= 3) {
      setError('You can attach up to three files.');
      return;
    }
    const params = new URLSearchParams({
      reportId,
      version: String(review.version),
      filename: file.name,
    });
    await mutate('Uploading', '/api/review/attachments?' + params, {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      body: file,
    });
  }

  function fieldControl(field: ReviewField) {
    const value = values[field.id];
    const descriptionSuffix =
      '\n\nIntake correlation: relay' + reportId.replaceAll('-', '');
    const fixedSuffix =
      field.id === 'description' &&
      displayValue(value).endsWith(descriptionSuffix)
        ? descriptionSuffix
        : '';
    const id = 'review-field-' + field.id;
    const invalid =
      missing.some((item) => item.id === field.id) ||
      !!review?.verification.issues.some((issue) => issue.field === field.id);
    const common = {
      id,
      disabled: !!busy || approved || !!field.readOnly || conflict,
      required: field.required,
      'aria-invalid': invalid,
      'aria-describedby': invalid ? id + '-issues' : undefined,
    };
    if (field.kind === 'textarea')
      return (
        <textarea
          {...common}
          rows={field.id === 'description' ? 14 : 4}
          value={
            fixedSuffix
              ? displayValue(value).slice(0, -fixedSuffix.length)
              : displayValue(value)
          }
          onChange={(e) => edit(field.id, e.target.value + fixedSuffix)}
        />
      );
    if (field.kind === 'select' || field.kind === 'multiselect') {
      const multiple = field.kind === 'multiselect';
      return (
        <select
          {...common}
          multiple={multiple}
          value={
            multiple
              ? Array.isArray(value)
                ? value.map(displayValue)
                : []
              : displayValue(value)
          }
          onChange={(e) =>
            edit(
              field.id,
              multiple
                ? Array.from(e.target.selectedOptions, (option) => option.value)
                : e.target.value,
            )
          }
        >
          {!multiple && (
            <option value="">Select {field.label.toLowerCase()}</option>
          )}
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      );
    }
    return (
      <input
        {...common}
        type={
          field.kind === 'number'
            ? 'number'
            : field.kind === 'date'
              ? 'date'
              : 'text'
        }
        step={field.kind === 'number' ? 'any' : undefined}
        value={displayValue(value)}
        onChange={(e) =>
          edit(
            field.id,
            field.kind === 'number'
              ? e.target.value === ''
                ? null
                : Number(e.target.value)
              : e.target.value,
          )
        }
      />
    );
  }

  if (!review && !error) {
    if (!['review_pending', 'awaiting_approval'].includes(reportState))
      return providerKey ? (
        <section
          id="ticket-request"
          className="ticket-review"
          tabIndex={-1}
          aria-label="Submitted request"
        >
          <div className="request-form-body">
            <h2>Your support request</h2>
            <div className="request-delivery">
              <CheckCircle2 size={22} />
              <div>
                <strong>{providerKey}</strong>
                <p>{providerStatus || 'Request created'}</p>
              </div>
              {providerUrl && (
                <a href={providerUrl} target="_blank" rel="noreferrer">
                  Open in Jira <ExternalLink size={14} />
                </a>
              )}
            </div>
            <p className="review-caption">
              This request was created before ticket previews were available.
              View its details in Jira.
            </p>
          </div>
        </section>
      ) : null;
    return (
      <section
        id="ticket-request"
        className="ticket-review"
        aria-label="Ticket review"
        tabIndex={-1}
      >
        <ReviewSteps active={1} />
        <output className="pending-note">
          <RefreshCw size={16} className="spin" />
          {loading
            ? 'Loading your ticket preview…'
            : 'Preparing Jira fields and verification. If this persists, retry from My requests or ask support.'}
        </output>
      </section>
    );
  }
  return (
    <section
      id="ticket-request"
      tabIndex={-1}
      className="ticket-review"
      aria-labelledby="review-title"
      aria-busy={!!busy}
    >
      <div className="request-portal-bar">
        <span>
          <Headphones size={17} /> IT service desk
        </span>
        <span>
          {mode === 'demo' ? 'Demo request' : 'Jira Service Management'}
        </span>
      </div>
      <div className="request-form-body">
        <div className="request-breadcrumb" aria-label="Request location">
          <span>Help center</span>
          <ChevronRight size={13} />
          <span>IT support</span>
          <ChevronRight size={13} />
          <span>{providerKey || 'New request'}</span>
        </div>
        <div className="section-heading request-heading">
          <div>
            <h2 id="review-title">
              {providerKey
                ? 'Your support request'
                : approved
                  ? 'Submitting your request'
                  : 'Review your request'}
            </h2>
            <p className="review-caption">
              {mode === 'demo'
                ? 'A simulated Jira form, filled from your conversation.'
                : approved
                  ? 'The details you approved, with delivery tracked below.'
                  : 'Filled from your conversation. Check the details before sending.'}
            </p>
          </div>
          {review && (
            <span
              className={'request-state-badge' + (approved ? ' approved' : '')}
            >
              {providerKey
                ? 'Submitted'
                : approved
                  ? 'Sending'
                  : 'Draft · not sent'}
            </span>
          )}
        </div>
        <ReviewSteps active={providerKey ? 4 : approved ? 3 : review ? 2 : 1} />
        {error && (
          <div className="review-alert" role="alert">
            <AlertCircle size={17} />
            <p>{error}</p>
          </div>
        )}
        {conflict && (
          <div className="review-conflict">
            <p>
              A newer ticket version is available. Loading it replaces your
              unsaved edits; copy any changes you want to keep first.
            </p>
            <button
              type="button"
              className="secondary"
              disabled={!!busy}
              onClick={() => {
                generation.current++;
                void load(true);
              }}
            >
              Discard edits and load latest
            </button>
          </div>
        )}
        {!review && error && (
          <button className="secondary" onClick={() => void load(true)}>
            Retry loading ticket
          </button>
        )}
        {review && (
          <>
            {approved && (
              <div className="request-delivery" aria-live="polite">
                {providerKey ? (
                  <CheckCircle2 size={22} />
                ) : (
                  <RefreshCw size={20} className="spin" />
                )}
                <div>
                  <strong>
                    {providerKey
                      ? `${providerKey} · ${providerStatus || 'Request created'}`
                      : 'Your approved request is queued for Jira'}
                  </strong>
                  <p>
                    {providerKey
                      ? 'Your request has been created. Support will pick it up from here.'
                      : 'The request reference will appear here once creation is confirmed.'}
                  </p>
                </div>
                {providerUrl && (
                  <a href={providerUrl} target="_blank" rel="noreferrer">
                    Open in Jira <ExternalLink size={14} />
                  </a>
                )}
              </div>
            )}
            <div className="request-type-label">What can we help you with?</div>
            <div className="request-type-card">
              <span className="request-type-icon">
                <Headphones size={23} />
              </span>
              <div>
                <strong>{review.form.requestTypeName}</strong>
                <p>
                  {mode === 'demo'
                    ? 'Simulated request type'
                    : 'Request type from your Jira service desk'}
                </p>
              </div>
              <span className="request-type-fixed">
                <LockKeyhole size={12} /> Request type
              </span>
            </div>
            <div className="request-person-routing">
              <div className="request-person">
                <span className="request-type-label">Requested by</span>
                <strong>
                  <UserRound size={16} /> {requesterName}
                </strong>
                <span className="review-caption">Your Relay account</span>
              </div>
              <dl className="review-routing">
                <div>
                  <dt>Suggested team</dt>
                  <dd>{review.team}</dd>
                </div>
                <div>
                  <dt>Priority</dt>
                  <dd>{review.priority}</dd>
                </div>
              </dl>
            </div>
            <div
              className={
                'review-verification ' +
                (dirty ? 'needs_changes' : review.verification.status)
              }
            >
              <div className="review-verification-heading">
                {review.verification.status === 'passed' ? (
                  <ShieldCheck size={19} />
                ) : (
                  <AlertCircle size={19} />
                )}
                <strong>
                  {review.verification.status === 'passed'
                    ? dirty
                      ? 'Changes need verification'
                      : 'Checked against your conversation'
                    : review.verification.status === 'unavailable'
                      ? 'Verifier unavailable — approval paused'
                      : 'A few details need attention'}
                </strong>
              </div>
              {!!review.verification.issues.length && (
                <ul>
                  {review.verification.issues.map((issue, i) => (
                    <li key={i}>{issue.message}</li>
                  ))}
                </ul>
              )}
              {!!review.verification.checks.length && (
                <details>
                  <summary>View verification details</summary>
                  <ul>
                    {review.verification.checks.map((check, i) => (
                      <li key={i}>{check}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
            {!!blockingUnsupported.length && (
              <div className="review-alert" role="alert">
                <AlertCircle size={18} />
                <p>
                  This request type has fields Relay cannot fill yet:{' '}
                  {blockingUnsupported.map((field) => field.label).join(', ')}.
                  Submission is paused until the form is supported.
                </p>
              </div>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void mutate(
                  'Verifying',
                  '/api/review',
                  json({ reportId, version: review.version, values }),
                );
              }}
              noValidate
            >
              <p className="request-required-note">
                Required fields are marked with{' '}
                <span aria-hidden="true">*</span>
                <span className="sr-only">an asterisk</span>
              </p>
              <div className="review-fields">
                {review.form.fields.map((field) => {
                  const issues = review.verification.issues.filter(
                    (issue) => issue.field === field.id,
                  );
                  const requiredMissing =
                    field.required && empty(values[field.id]);
                  return (
                    <div className="review-field" key={field.id}>
                      <label htmlFor={'review-field-' + field.id}>
                        {field.label}
                        {field.required && (
                          <span className="review-required">
                            <span aria-hidden="true"> *</span>
                            <span className="sr-only"> (required)</span>
                          </span>
                        )}
                        {field.readOnly && (
                          <span className="review-caption"> · Read only</span>
                        )}
                      </label>
                      {fieldControl(field)}
                      {field.id === 'description' &&
                        displayValue(values.description).endsWith(
                          '\n\nIntake correlation: relay' +
                            reportId.replaceAll('-', ''),
                        ) && (
                          <details className="review-reference">
                            <summary>Included reference · read only</summary>
                            <code>
                              {'Intake correlation: relay' +
                                reportId.replaceAll('-', '')}
                            </code>
                            <p>
                              This reference is included at the end of the Jira
                              description to connect it to your approved ticket.
                            </p>
                          </details>
                        )}
                      {(requiredMissing || !!issues.length) && (
                        <div
                          className="review-field-issues"
                          id={'review-field-' + field.id + '-issues'}
                        >
                          {requiredMissing && (
                            <p>Please fill in {field.label.toLowerCase()}.</p>
                          )}
                          {issues.map((issue, i) => (
                            <p key={i}>{issue.message}</p>
                          ))}
                        </div>
                      )}
                      {field.kind === 'multiselect' && !approved && (
                        <p className="review-caption">
                          Use Ctrl or Command to select more than one option.
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="review-attachments">
                <h3>
                  <Paperclip size={17} />
                  Attachments
                  {review.form.attachmentsRequired && (
                    <span className="review-required">(required)</span>
                  )}
                </h3>
                <p className="review-caption">
                  {approved
                    ? review.attachments.length
                      ? 'Delivery status for the files included with your approval.'
                      : 'No attachments were included with this request.'
                    : 'Screenshots or logs help support understand the issue.'}
                </p>
                {!approved &&
                  review.form.attachmentsRequired &&
                  !review.attachments.length && (
                    <p className="review-field-issues">
                      This request type requires an attachment. Add at least one
                      file before approving your ticket.
                    </p>
                  )}
                {!!review.attachments.length && (
                  <ul>
                    {review.attachments.map((file) => (
                      <li key={file.id}>
                        <FileText size={20} className="attachment-file-icon" />
                        <div>
                          <strong>{file.filename}</strong>
                          <span>
                            {Math.max(1, Math.ceil(file.size / 1024))} KB ·{' '}
                            {attachmentLabels[file.state]}
                          </span>
                          {file.error && (
                            <p className="review-field-issues">{file.error}</p>
                          )}
                        </div>
                        {!approved && (
                          <button
                            type="button"
                            className="secondary"
                            disabled={!!busy || dirty || conflict}
                            aria-label={'Remove ' + file.filename}
                            onClick={() => {
                              const params = new URLSearchParams({
                                reportId,
                                version: String(review.version),
                                id: file.id,
                              });
                              void mutate(
                                'Removing',
                                '/api/review/attachments?' + params,
                                { method: 'DELETE' },
                              );
                            }}
                          >
                            <Trash2 size={16} />
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {!approved &&
                  (review.form.attachmentsAllowed ? (
                    <>
                      <input
                        ref={fileInput}
                        type="file"
                        className="sr-only"
                        tabIndex={-1}
                        aria-label="Choose an attachment"
                        accept=".pdf,.png,.jpg,.jpeg,.txt,.log"
                        disabled={
                          !!busy ||
                          dirty ||
                          conflict ||
                          review.attachments.length >= 3
                        }
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          e.target.value = '';
                          if (file) void attach(file);
                        }}
                      />
                      <div
                        className={
                          'attachment-dropzone' + (dragging ? ' dragging' : '')
                        }
                        onDragOver={(e) => {
                          e.preventDefault();
                          if (
                            !busy &&
                            !dirty &&
                            !conflict &&
                            review.attachments.length < 3
                          )
                            setDragging(true);
                        }}
                        onDragLeave={(e) => {
                          if (
                            !e.currentTarget.contains(e.relatedTarget as Node)
                          )
                            setDragging(false);
                        }}
                        onDrop={(e) => {
                          e.preventDefault();
                          setDragging(false);
                          if (e.dataTransfer.files.length > 1) {
                            setError(
                              'Please add one file at a time, up to three files.',
                            );
                            return;
                          }
                          const file = e.dataTransfer.files[0];
                          if (file) void attach(file);
                        }}
                      >
                        <Upload size={24} />
                        <p>
                          Drag a file here or{' '}
                          <button
                            type="button"
                            className="attachment-browse"
                            disabled={
                              !!busy ||
                              dirty ||
                              conflict ||
                              review.attachments.length >= 3
                            }
                            onClick={() => fileInput.current?.click()}
                          >
                            browse files
                          </button>
                        </p>
                        <span>
                          PDF, PNG, JPEG, TXT, LOG · Up to 3 files, 5 MB each
                        </span>
                      </div>
                      <p className="review-caption">
                        Files go to Jira after approval. AI checks do not
                        include file contents.
                      </p>
                      {dirty && (
                        <p className="review-caption">
                          Save your edits before changing attachments.
                        </p>
                      )}
                    </>
                  ) : (
                    <p className="review-caption">
                      Attachments are unavailable for this request type.
                    </p>
                  ))}
              </div>
              {!approved && (
                <div className="review-footer">
                  <div className="review-submit-note">
                    <LockKeyhole size={16} />
                    <p>
                      {dirty
                        ? 'You have unsaved changes.'
                        : canApprove
                          ? 'Ready for your approval.'
                          : 'Review the checks above before submitting.'}
                      <span>Only you can approve and send this request.</span>
                    </p>
                  </div>
                  <div className="review-actions">
                    {(dirty || review.verification.status !== 'passed') && (
                      <button
                        type="submit"
                        className="secondary"
                        disabled={!!busy || conflict}
                      >
                        {busy === 'Verifying' ? (
                          <RefreshCw size={16} className="spin" />
                        ) : (
                          <ShieldCheck size={16} />
                        )}
                        {busy === 'Verifying'
                          ? 'Verifying…'
                          : 'Save and recheck'}
                      </button>
                    )}
                    <button
                      type="button"
                      className="primary"
                      disabled={!canApprove}
                      onClick={() =>
                        void mutate(
                          'Approving',
                          '/api/review/approve',
                          json({ reportId, version: review.version }),
                          true,
                        )
                      }
                    >
                      <CheckCircle2 size={16} />
                      {busy === 'Approving'
                        ? 'Submitting…'
                        : mode === 'demo'
                          ? 'Approve demo request'
                          : 'Approve and send to Jira'}
                    </button>
                  </div>
                  {dirty && (
                    <p className="review-caption">
                      You have unsaved edits. Save and recheck before approval.
                    </p>
                  )}
                </div>
              )}
            </form>
            {approved && (
              <p className="review-approved">
                <CheckCircle2 size={17} />
                Approved
                {review.approvedAt
                  ? ' ' + new Date(review.approvedAt).toLocaleString()
                  : ''}
                . Approved details are read only.
              </p>
            )}
          </>
        )}
        <output className="review-notice">
          {busy && busy !== 'Verifying' && busy !== 'Approving'
            ? busy + '…'
            : notice}
        </output>
      </div>
    </section>
  );
}

function ReviewSteps({ active }: { active: number }) {
  return (
    <ol className="review-steps" aria-label="Ticket progress">
      {[
        'Describe issue',
        'Verify details',
        'Review & approve',
        'Submitted',
      ].map((step, i) => (
        <li
          key={step}
          aria-current={i === active ? 'step' : undefined}
          className={i < active ? 'complete' : i === active ? 'current' : ''}
        >
          <span>{i < active ? <CheckCircle2 size={13} /> : i + 1}</span>
          {step}
          {i < 3 && (
            <span className="review-step-arrow" aria-hidden="true">
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
