'use client';
import {
  ArrowUpRight,
  ArrowUp,
  ShieldCheck,
  Radio,
  LifeBuoy,
  Check,
  MessageSquare,
  Inbox,
  SlidersHorizontal,
  Network,
  Laptop,
  KeyRound,
  CircleHelp,
  Plus,
  RefreshCw,
  ChevronDown,
  Clock,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  BookOpen,
  Lock,
} from 'lucide-react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { useState, useEffect, useCallback, useRef } from 'react';
import { TicketReview } from '@/components/ticket-review';
import { PipelineChip } from '@/components/pipeline-chip';
import { fmtDateTime } from '@/lib/format';
import {
  teams,
  type Report,
  type User,
  type Message,
  type Source,
  type Operation,
  type Trace,
} from '@/lib/domain';
type Bootstrap = {
  user: User;
  profiles: { id: string; name: string; role: string }[];
  incidents: Source[];
  mode: string;
};
type Detail = {
  report: Report;
  messages: Message[];
  sources: Source[];
  operations: Operation[];
  events: { id: string; kind: string; detail: unknown; created_at: string }[];
  trace?: Trace;
};
type Health = {
  mode: string;
  worker: { healthy: boolean; last_seen?: string };
  counts: { state: string; count: number }[];
  jira: string;
  model: string;
  security: string;
};
const labels: Record<string, string> = {
  draft: 'Draft saved',
  processing: 'Checking your issue',
  awaiting_response: 'Waiting for you',
  awaiting_clarification: 'One question',
  related_suggested: 'Related advisory',
  related_reported: 'Following advisory',
  review_pending: 'Verifier checking',
  awaiting_approval: 'Review ticket',
  submission_pending: 'Sending to support',
  created: 'Request created',
  resolved: 'Resolution saved',
  operator_review: 'Needs operator review',
};
async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok)
    throw new Error(data.error ?? 'Something went wrong. Please retry.');
  return data;
}
export default function Home() {
  const [boot, setBoot] = useState<Bootstrap | null>(null),
    [tab, setTab] = useState('chat'),
    [text, setText] = useState(''),
    [detail, setDetail] = useState<Detail | null>(null),
    [reports, setReports] = useState<Report[]>([]),
    [health, setHealth] = useState<Health | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [loginToken, setLoginToken] = useState(''),
    [filter, setFilter] = useState('all');
  const [correctionTeam, setCorrectionTeam] = useState<string>('Service Desk'),
    [correctionPriority, setCorrectionPriority] = useState<string>('normal'),
    [reason, setReason] = useState('');
  const pending = useRef<{ body: unknown; signature: string } | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const bootstrap = useCallback(async () => {
    try {
      setBoot(await api<Bootstrap>('/api/bootstrap'));
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    const initial = setTimeout(() => void bootstrap(), 0);
    return () => clearTimeout(initial);
  }, [bootstrap]);
  const loadDetail = useCallback(async (id: string) => {
    const d = await api<Detail>('/api/reports?id=' + id);
    setDetail(d);
    return d;
  }, []);
  const detailId = detail?.report.id;
  const reportState = detail?.report.state;
  const refresh = useCallback(async () => {
    if (!boot) return;
    try {
      const rows = await api<{ reports: Report[] }>(
        '/api/reports' + (tab === 'ops' ? '?all=1' : ''),
      );
      setReports(rows.reports);
      if (detailId) await loadDetail(detailId);
      if (tab === 'ops') setHealth(await api<Health>('/api/operations'));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [boot, tab, detailId, loadDetail]);
  useEffect(() => {
    const initial = setTimeout(() => void refresh(), 0);
    const timer = setInterval(() => void refresh(), 4000);
    return () => {
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, [refresh]);
  useEffect(() => {
    if (
      reportState &&
      ['awaiting_response', 'awaiting_clarification'].includes(reportState)
    )
      scroll.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [detail?.messages.length, reportState]);
  async function switchUser(id: string | null) {
    if (!id) return;
    setBusy(true);
    try {
      await api('/api/session', { userId: id });
      setDetail(null);
      setReports([]);
      setTab('chat');
      setText('');
      pending.current = null;
      await bootstrap();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function send(action = 'message', override?: string) {
    if (busy) return;
    const content = override ?? text;
    if (!detail && !content.trim()) {
      setError(
        'Describe your issue first so support has something to work with.',
      );
      return;
    }
    if (action === 'message' && !content.trim()) return;
    setBusy(true);
    setError('');
    const signature = JSON.stringify({
      text: content,
      action,
      reportId: detail?.report.id,
    });
    if (pending.current?.signature !== signature)
      pending.current = {
        signature,
        body: {
          text: content,
          action,
          reportId: detail?.report.id,
          submissionKey: crypto.randomUUID(),
        },
      };
    try {
      const r = await api<{ id: string }>('/api/intake', pending.current.body);
      await loadDetail(r.id);
      setText('');
      pending.current = null;
      setTab('chat');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function operate(action: string) {
    if (!detail) return;
    setBusy(true);
    setError('');
    try {
      await api('/api/operations', {
        reportId: detail.report.id,
        action,
        ...(action === 'correct'
          ? { team: correctionTeam, priority: correctionPriority, reason }
          : {}),
      });
      await refresh();
      setReason('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function newChat() {
    setDetail(null);
    setText('');
    setError('');
    pending.current = null;
    setTab('chat');
  }
  useEffect(() => {
    const ctx = (
      document as Document & {
        modelContext?: {
          registerTool: (tool: unknown, options: unknown) => void;
        };
      }
    ).modelContext;
    if (!ctx) return;
    const lifecycle = new AbortController();
    try {
      ctx.registerTool(
        {
          name: 'start_it_report',
          description:
            'Start a new IT report draft with supplied text. Does not submit the report.',
          inputSchema: {
            type: 'object',
            properties: {
              text: { type: 'string', minLength: 1, maxLength: 6000 },
            },
            required: ['text'],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: true },
          execute(input: unknown) {
            const v = input as { text?: unknown };
            if (
              typeof v?.text !== 'string' ||
              !v.text.trim() ||
              v.text.length > 6000
            )
              throw new Error('Provide 1–6000 characters.');
            setDetail(null);
            setText(v.text);
            setTab('chat');
            return { staged: true, submitted: false };
          },
        },
        { signal: lifecycle.signal },
      );
    } catch {
      /* Optional browser API is unavailable. */
    }
    return () => lifecycle.abort();
  }, []);
  const r = detail?.report,
    waiting =
      r &&
      ['awaiting_response', 'awaiting_clarification', 'processing'].includes(
        r.state,
      ),
    showComposer = !r || r.state === 'awaiting_clarification';
  const hasReview =
    !!r &&
    r.owner_id === boot?.user.id &&
    ([
      'review_pending',
      'awaiting_approval',
      'submission_pending',
      'created',
    ].includes(r.state) ||
      !!r.provider_key);
  const list = reports.filter(
    (x) =>
      filter === 'all' ||
      (filter === 'review' && x.state === 'operator_review') ||
      (filter === 'open' && x.state !== 'resolved') ||
      (filter === 'resolved' && x.state === 'resolved'),
  );
  const decisionPanel = detail && (
    <div className="decision-panel">
      <div className="section-heading">
        <h2>Decision record</h2>
        <span className="small">
          {detail.report.decision.model} · {detail.report.decision.version}
        </span>
      </div>
      <div className="fact-grid">
        {Object.entries(detail.report.decision.facts).map(([key, f]) => (
          <div key={key}>
            <span className="fact-label">{key.replace(/([A-Z])/g, ' $1')}</span>
            <strong>{f.value ?? 'Unknown'}</strong>
            <span className={'origin ' + f.origin}>
              {f.origin.replace('_', ' ')}
            </span>
            <span className="small">
              {f.evidenceIds.join(', ') || 'No evidence supplied'}
            </span>
          </div>
        ))}
      </div>
      <div className="reason-codes">
        <strong>Policy reasons</strong>
        {detail.report.decision.reasons.map((x) => (
          <code key={x}>{x}</code>
        ))}
      </div>
      <p className="small">
        Suggested steps: {detail.report.offered.join(', ') || 'None'} ·
        Confirmed attempted: {detail.report.attempted.join(', ') || 'None'}
      </p>
      <p className="small">
        Route:{' '}
        {detail.report.decision.accepted
          ? 'Accepted'
          : 'Abstained to Service Desk'}
        . Candidate scores are uncalibrated ranking values.
      </p>
    </div>
  );
  return (
    <main className={hasReview && tab === 'chat' ? 'request-mode' : undefined}>
      <header className="topbar">
        <button className="brand" onClick={newChat} aria-label="Relay home">
          <span className="brand-icon">
            <ArrowUpRight size={23} />
          </span>
          relay<span className="brand-label">EMPLOYEE SUPPORT</span>
        </button>
        <span className="demo-pill">
          <i />
          {!boot
            ? 'Workspace access'
            : boot.mode === 'live'
              ? 'Live mode'
              : 'Local demo'}
        </span>
      </header>
      {!boot ? (
        <section className="empty">
          <ShieldCheck size={32} />
          <h1>
            {error === 'Unauthorized'
              ? 'Sign in to Relay'
              : 'Connecting to your workspace'}
          </h1>
          {error === 'Unauthorized' ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await api('/api/session', { token: loginToken });
                  setLoginToken('');
                  await bootstrap();
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              <label htmlFor="session">
                Session token issued by your operator
              </label>
              <input
                id="session"
                type="password"
                value={loginToken}
                onChange={(e) => setLoginToken(e.target.value)}
              />
              <button className="primary">Sign in</button>
            </form>
          ) : (
            <>
              <p>{error || 'Loading your saved reports…'}</p>
              {error && (
                <button className="secondary" onClick={bootstrap}>
                  Try again
                </button>
              )}
            </>
          )}
        </section>
      ) : (
        <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
          <div className="navrow">
            <TabsList variant="line">
              <TabsTrigger value="chat">
                <MessageSquare />
                Get help
              </TabsTrigger>
              <TabsTrigger value="requests">
                <Inbox />
                My requests
              </TabsTrigger>
              {boot.user.role === 'operator' && (
                <TabsTrigger value="ops">
                  <SlidersHorizontal />
                  Operations
                </TabsTrigger>
              )}
            </TabsList>
            <div className="profile">
              <span className="avatar">
                {boot.user.name
                  .split(' ')
                  .map((s) => s[0])
                  .join('')}
              </span>
              {boot.mode === 'demo' ? (
                <Select
                  value={boot.user.id}
                  onValueChange={switchUser}
                  disabled={busy}
                >
                  <SelectTrigger aria-label="Demo employee profile">
                    <SelectValue>{boot.user.name}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {boot.profiles.map((u) => (
                      <SelectItem key={u.id} value={u.id}>
                        {u.name}
                        {u.role === 'operator' ? ' · Operator' : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <span>{boot.user.name}</span>
              )}
            </div>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              <AlertCircle size={18} />
              <span>{error}</span>
              <button onClick={() => setError('')} aria-label="Dismiss error">
                ×
              </button>
            </div>
          )}
          <TabsContent value="chat">
            <div
              className={'workspace' + (hasReview ? ' request-workspace' : '')}
            >
              <section className="intake">
                {!r ? (
                  <>
                    <div className="eyebrow">IT HELP, WITHOUT THE HANDOFFS</div>
                    <h1>What is going wrong?</h1>
                    <p className="lede">
                      Tell us what happened. We’ll help you fix it
                      <br />
                      or get it to the right people.
                    </p>
                  </>
                ) : (
                  <>
                    <div className="section-heading">
                      <div className="eyebrow">
                        {hasReview ? 'YOUR ISSUE' : 'YOUR CONVERSATION'}
                      </div>
                      <button className="text-button" onClick={newChat}>
                        <Plus size={16} />
                        New issue
                      </button>
                    </div>
                    <h1 className="conversation-title">{r.summary}</h1>
                    <div className="report-meta">
                      <span className={'status ' + r.state}>
                        {labels[r.state]}
                      </span>
                      <span>{fmtDateTime(r.created_at)}</span>
                      <PipelineChip pipeline={r.decision.pipeline} />
                      {r.decision.visibility === 'restricted' && (
                        <span>
                          <Lock size={13} />
                          Restricted
                        </span>
                      )}
                    </div>
                    {hasReview && (
                      <div className="request-original">
                        <span className="message-label">
                          <MessageSquare size={15} /> You reported
                        </span>
                        <p>
                          {detail.messages.find((m) => m.role === 'user')?.body}
                        </p>
                        <a href="#ticket-request">
                          {r.provider_key
                            ? 'View submitted request'
                            : 'Review your request'}{' '}
                          <ArrowUpRight size={14} />
                        </a>
                      </div>
                    )}
                    <details
                      className="conversation-history"
                      open={hasReview ? undefined : true}
                    >
                      <summary>
                        <MessageSquare size={15} /> Conversation history{' '}
                        <ChevronDown size={14} />
                      </summary>
                      <div
                        className="messages"
                        aria-live={hasReview ? 'off' : 'polite'}
                      >
                        {detail.messages
                          .filter(
                            (m) =>
                              !m.body.startsWith(
                                'Your message is saved. Checking',
                              ),
                          )
                          .map((m) => (
                            <div key={m.id} className={'message ' + m.role}>
                              <div className="message-label">
                                {m.role === 'assistant' ? (
                                  <>
                                    <span className="mini-brand">
                                      <ArrowUpRight size={13} />
                                    </span>
                                    Relay
                                  </>
                                ) : (
                                  'You'
                                )}
                              </div>
                              <p>{m.body}</p>
                            </div>
                          ))}
                        <div ref={scroll} />
                      </div>
                    </details>
                    {r.state === 'awaiting_response' && (
                      <div className="response-actions">
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() => send('fixed', '')}
                        >
                          <Check size={16} />
                          Fixed it
                        </button>
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() => send('broken', '')}
                        >
                          Still broken
                        </button>
                      </div>
                    )}
                    {r.state === 'related_suggested' && (
                      <div className="related-card">
                        <span className="tag amber">ACTIVE ADVISORY</span>
                        <h3>{r.decision.related?.title}</h3>
                        <p>{r.decision.related?.body}</p>
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() => send('follow', '')}
                        >
                          Follow this advisory
                        </button>
                        <span className="small">
                          In-app follow · your report stays separate
                        </span>
                      </div>
                    )}
                    {r.provider_key && !hasReview && (
                      <div className="saved-ticket">
                        <CheckCircle2 size={22} />
                        <div>
                          <strong>{r.provider_key}</strong>
                          <p>
                            {r.provider_team ?? 'Routing update pending'} ·{' '}
                            {r.provider_status}
                          </p>
                          <span className="small">
                            {r.mode === 'demo'
                              ? 'Simulated provider request'
                              : 'Jira Service Management'}{' '}
                            ·{' '}
                            {r.synced_at
                              ? 'Synced ' + fmtDateTime(r.synced_at)
                              : 'Awaiting synchronization'}
                          </span>
                        </div>
                        {r.provider_url && (
                          <a
                            href={r.provider_url}
                            target="_blank"
                            rel="noreferrer"
                            aria-label="Open Jira request"
                          >
                            <ExternalLink size={17} />
                          </a>
                        )}
                      </div>
                    )}
                    {r.state === 'resolved' && (
                      <div className="saved-ticket">
                        <CheckCircle2 size={22} />
                        <div>
                          <strong>Resolution saved</strong>
                          <p>
                            {r.provider_key
                              ? 'Resolved by provider'
                              : 'Confirmed by you. No ticket needed.'}
                          </p>
                        </div>
                      </div>
                    )}
                    {r.state === 'processing' && (
                      <p className="pending-note">
                        <RefreshCw size={15} />
                        Your message is saved. Checking approved sources…
                      </p>
                    )}
                  </>
                )}
                {showComposer && (
                  <form
                    className="composer"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void send();
                    }}
                  >
                    <label className="sr-only" htmlFor="issue">
                      {r
                        ? 'Answer the clarification'
                        : 'Describe your IT issue'}
                    </label>
                    <textarea
                      id="issue"
                      value={text}
                      maxLength={6000}
                      onChange={(e) => setText(e.target.value)}
                      placeholder={
                        r
                          ? 'Tell us which service is affected…'
                          : 'For example, my VPN stopped working after I changed my password…'
                      }
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                          e.preventDefault();
                          void send();
                        }
                      }}
                    />
                    <div className="composer-bottom">
                      <span>
                        <ShieldCheck size={15} />
                        Your report is saved privately
                      </span>
                      <button
                        className="send"
                        disabled={busy || !text.trim()}
                        aria-label="Submit issue"
                      >
                        {busy ? (
                          <RefreshCw size={18} className="spin" />
                        ) : (
                          <ArrowUp size={20} />
                        )}
                      </button>
                    </div>
                  </form>
                )}
                {!r && (
                  <>
                    <p className="consent">
                      If you need support, we’ll prepare a ticket for you to
                      review, edit, and approve before it goes to Jira.
                    </p>
                    <div className="section-label">
                      START WITH SOMETHING COMMON
                    </div>
                    <div className="suggestions">
                      {[
                        {
                          Icon: Network,
                          label: 'My Wi-Fi keeps disconnecting',
                        },
                        {
                          Icon: KeyRound,
                          label: 'VPN broke after I changed my password',
                        },
                        {
                          Icon: Laptop,
                          label: 'My external display is flickering',
                        },
                      ].map(({ Icon, label }) => (
                        <button key={label} onClick={() => setText(label)}>
                          <Icon size={19} />
                          <span>{label}</span>
                          <ArrowUpRight size={16} />
                        </button>
                      ))}
                    </div>
                  </>
                )}
                {(!r ||
                  ![
                    'created',
                    'resolved',
                    'submission_pending',
                    'operator_review',
                    'review_pending',
                    'awaiting_approval',
                  ].includes(r.state)) && (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => send('support')}
                  >
                    <LifeBuoy size={17} />
                    Prepare ticket
                  </button>
                )}
                {r && (
                  <details className="sources-disclosure">
                    <summary>
                      <BookOpen size={16} />
                      Context used
                      <ChevronDown size={14} />
                    </summary>
                    <p className="small">
                      {boot.mode === 'demo'
                        ? 'Simulated directory and device inventory'
                        : 'Live context'}{' '}
                      · captured {fmtDateTime(r.updated_at)}. Optional unknowns
                      never block support.
                    </p>
                    <div className="context-facts">
                      <span>
                        {r.decision.facts.device?.value ?? 'Device unknown'}
                      </span>
                      <span>
                        {r.decision.facts.location?.value ?? 'Location unknown'}
                      </span>
                    </div>
                    {detail.sources.map((s) => (
                      <div className="source" key={s.id}>
                        <strong>{s.title}</strong>
                        <span className="small">
                          {s.id} · {s.kind} · {fmtDateTime(s.updated_at)}
                          {boot.mode === 'demo' ? ' · synthetic' : ''}
                        </span>
                        <p>{s.body}</p>
                      </div>
                    ))}
                  </details>
                )}
              </section>
              {hasReview && r ? (
                <TicketReview
                  key={r.id}
                  reportId={r.id}
                  reportState={r.state}
                  mode={boot.mode}
                  requesterName={boot.user.name}
                  providerKey={r.provider_key}
                  providerUrl={r.provider_url}
                  providerStatus={r.provider_status}
                  onApproved={() => loadDetail(r.id)}
                />
              ) : (
                <aside className="context">
                  <div className="context-heading">
                    <Radio size={18} />
                    <h2>Service pulse</h2>
                    <span className="live-dot" />
                  </div>
                  {boot.incidents.length ? (
                    boot.incidents.map((i) => (
                      <div className="advisory" key={i.id}>
                        <span className="tag amber">INVESTIGATING</span>
                        <h3>{i.title}</h3>
                        <p>{i.body}</p>
                        <span className="small">
                          {boot.mode === 'demo'
                            ? 'Simulated advisory'
                            : 'Approved advisory'}{' '}
                          · {fmtDateTime(i.updated_at)}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="advisory">
                      <span className="operational">
                        <Check size={15} />
                        No active advisories for your location
                      </span>
                    </div>
                  )}
                  <div className="service-list">
                    {['Corporate VPN', 'Single sign-on', 'Office Wi-Fi'].map(
                      (s) => (
                        <div key={s}>
                          <span>{s}</span>
                          <span className="operational">
                            <Check size={14} />
                            {boot.mode === 'demo'
                              ? 'Operational'
                              : 'No advisory'}
                          </span>
                        </div>
                      ),
                    )}
                  </div>
                  <div className="context-note">
                    <ShieldCheck size={20} />
                    <div>
                      <h3>A little context. Less repetition.</h3>
                      <p>
                        {boot.mode === 'demo'
                          ? 'We can use your demo profile, managed device, and approved help articles.'
                          : 'We use only approved sources available to your account.'}{' '}
                        Sources appear with each response.
                      </p>
                    </div>
                  </div>
                  <div className="demo-note">
                    <CircleHelp size={17} />
                    <p>
                      {boot.mode === 'demo'
                        ? 'You’re exploring a demo with synthetic employee and service data. No real tickets are created.'
                        : 'Your messages are stored for support and may be processed by the configured OpenAI model.'}
                    </p>
                  </div>
                  {waiting && (
                    <p className="pending-note">
                      <Clock size={15} />
                      This stays open until you respond.
                    </p>
                  )}
                </aside>
              )}
            </div>
          </TabsContent>
          <TabsContent value="requests">
            <section className="list-page">
              <div className="page-heading">
                <div>
                  <div className="eyebrow">YOUR SUPPORT HISTORY</div>
                  <h1>My requests</h1>
                  <p className="lede">
                    Every issue has a record. Even the ones fixed here.
                  </p>
                </div>
                <button className="primary" onClick={newChat}>
                  <Plus size={17} />
                  New issue
                </button>
              </div>
              <div className="list-toolbar">
                <span>{reports.length} saved conversations</span>
                <Select
                  value={filter}
                  onValueChange={(v) => setFilter(String(v))}
                >
                  <SelectTrigger aria-label="Filter requests">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All outcomes</SelectItem>
                    <SelectItem value="open">Open</SelectItem>
                    <SelectItem value="resolved">Resolved</SelectItem>
                    <SelectItem value="review">Needs review</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {list.length ? (
                <div className="request-list">
                  {list.map((item) => (
                    <button
                      className="request-row"
                      key={item.id}
                      onClick={() => {
                        void loadDetail(item.id);
                        setTab('chat');
                      }}
                    >
                      <span className="request-icon">
                        {item.state === 'resolved' ? (
                          <CheckCircle2 />
                        ) : (
                          <MessageSquare />
                        )}
                      </span>
                      <div>
                        <h3>{item.summary}</h3>
                        <span>
                          {item.provider_key ?? 'Conversation saved'} ·{' '}
                          {fmtDateTime(item.created_at)}
                          <PipelineChip pipeline={item.decision.pipeline} />
                        </span>
                      </div>
                      <span className={'status ' + item.state}>
                        {labels[item.state]}
                      </span>
                      <ArrowUpRight size={18} />
                    </button>
                  ))}
                </div>
              ) : (
                <div className="empty compact">
                  <Inbox size={32} />
                  <h2>No requests here yet</h2>
                  <p>
                    Your conversations and saved resolutions will appear here.
                  </p>
                </div>
              )}
            </section>
          </TabsContent>
          {boot.user.role === 'operator' && (
            <TabsContent value="ops">
              <section className="list-page operations">
                <div className="page-heading">
                  <div>
                    <div className="eyebrow">SUPPORT OPERATIONS</div>
                    <h1>Keep every handoff moving.</h1>
                  </div>
                  <button className="secondary" onClick={() => void refresh()}>
                    <RefreshCw size={16} />
                    Refresh
                  </button>
                </div>
                <div className="health-grid">
                  <div>
                    <span>Worker</span>
                    <strong
                      className={health?.worker.healthy ? 'good' : 'warn'}
                    >
                      {health?.worker.healthy ? 'Running' : 'Not responding'}
                    </strong>
                    <small>
                      {health?.worker.last_seen
                        ? fmtDateTime(health.worker.last_seen)
                        : 'Start the background worker'}
                    </small>
                  </div>
                  <div>
                    <span>Jira connector</span>
                    <strong>{health?.jira ?? 'Checking'}</strong>
                    <small>
                      {health?.mode === 'demo'
                        ? 'No external requests'
                        : 'Sandbox smoke test required'}
                    </small>
                  </div>
                  <div>
                    <span>Model</span>
                    <strong>{health?.model ?? 'Checking'}</strong>
                    <small>Policy northstar-1.0</small>
                  </div>
                  <div>
                    <span>Pending review</span>
                    <strong>
                      {
                        reports.filter((x) => x.state === 'operator_review')
                          .length
                      }
                    </strong>
                    <small>Security reports remain restricted</small>
                  </div>
                </div>
                <div className="operator-table">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>REPORT</TableHead>
                        <TableHead>DESTINATION</TableHead>
                        <TableHead>PRIORITY</TableHead>
                        <TableHead>STATE</TableHead>
                        <TableHead>REVIEW</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {reports.map((item) => (
                        <TableRow
                          key={item.id}
                          className={r?.id === item.id ? 'selected-row' : ''}
                        >
                          <TableCell>
                            <button
                              onClick={() => {
                                void loadDetail(item.id);
                                setCorrectionTeam(
                                  item.provider_team ?? item.decision.team,
                                );
                                setCorrectionPriority(
                                  item.provider_priority ??
                                    item.decision.priority,
                                );
                              }}
                              className="table-link"
                            >
                              {item.summary}
                            </button>
                            <span className="small">
                              {item.provider_key ??
                                item.owner_id + ' · local record'}
                            </span>
                          </TableCell>
                          <TableCell>
                            {item.provider_team ?? item.decision.team}
                            <PipelineChip pipeline={item.decision.pipeline} />
                          </TableCell>
                          <TableCell>
                            <span
                              className={'priority ' + item.decision.priority}
                            >
                              {item.provider_priority ?? item.decision.priority}
                            </span>
                          </TableCell>
                          <TableCell>
                            <span className={'status ' + item.state}>
                              {labels[item.state]}
                            </span>
                          </TableCell>
                          <TableCell>
                            {item.acknowledged_at ? (
                              <span className="good">Acknowledged</span>
                            ) : (
                              <span className="warn">Unacknowledged</span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!reports.length && (
                    <p className="empty-table">
                      No reports yet. Submit an employee issue to populate the
                      queue.
                    </p>
                  )}
                </div>
                {detail && (
                  <section className="operator-detail">
                    <div className="section-heading">
                      <h2>{detail.report.summary}</h2>
                      <button
                        className="secondary"
                        disabled={busy}
                        onClick={() => operate('acknowledge')}
                      >
                        <Check size={16} />
                        Acknowledge
                      </button>
                    </div>
                    {decisionPanel}
                    <div className="correction">
                      <h3>Correct provider routing</h3>
                      <p>
                        Changes appear as applied after the provider confirms
                        them. Original decisions stay in the audit record.
                      </p>
                      <div className="correction-fields">
                        <Select
                          value={correctionTeam}
                          onValueChange={(v) => setCorrectionTeam(String(v))}
                        >
                          <SelectTrigger aria-label="Corrected support team">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {teams.map((t) => (
                              <SelectItem key={t} value={t}>
                                {t}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Select
                          value={correctionPriority}
                          onValueChange={(v) =>
                            setCorrectionPriority(String(v))
                          }
                        >
                          <SelectTrigger aria-label="Corrected priority">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {['normal', 'elevated', 'urgent'].map((t) => (
                              <SelectItem key={t} value={t}>
                                {t}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <input
                          aria-label="Reason for correction"
                          placeholder="Why is this correction needed?"
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                        />
                        <button
                          className="primary"
                          disabled={busy || !r?.provider_key || !reason.trim()}
                          onClick={() => operate('correct')}
                        >
                          Queue correction
                        </button>
                      </div>
                    </div>
                    <div className="connector-log">
                      <div className="section-heading">
                        <h3>Connector operations</h3>
                        <div className="button-group">
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => operate('retry')}
                          >
                            Recover failed operations
                          </button>
                          <button
                            className="secondary"
                            disabled={busy || !r?.provider_key}
                            onClick={() => operate('refresh')}
                          >
                            Sync provider
                          </button>
                        </div>
                      </div>
                      {detail.operations.length ? (
                        detail.operations.map((op) => (
                          <div className="operation-row" key={op.id}>
                            <span className={'status ' + op.state}>
                              {op.state}
                            </span>
                            <strong>{op.kind}</strong>
                            <span>
                              {op.external_key ?? 'No external reference'}
                            </span>
                            <p>
                              {op.last_error ??
                                'No integration errors recorded.'}
                            </p>
                            <code>{op.operation_key}</code>
                          </div>
                        ))
                      ) : (
                        <p className="small">
                          No provider operation was needed.
                        </p>
                      )}
                    </div>
                    <details className="sources-disclosure">
                      <summary>
                        <BookOpen size={16} />
                        Approved sources and operator events
                        <ChevronDown size={14} />
                      </summary>
                      {detail.sources.map((s) => (
                        <div key={s.id} className="source">
                          <strong>
                            {s.id} · {s.title}
                          </strong>
                          <p>{s.body}</p>
                        </div>
                      ))}
                      {detail.events.map((e) => (
                        <div className="source" key={e.id}>
                          <strong>{e.kind}</strong>
                          <span className="small">
                            {fmtDateTime(e.created_at)}
                          </span>
                          <pre>{JSON.stringify(e.detail, null, 2)}</pre>
                        </div>
                      ))}
                    </details>
                  </section>
                )}
              </section>
            </TabsContent>
          )}
        </Tabs>
      )}
      <footer>
        <span>relay / IT support, connected.</span>
        <span>
          {!boot
            ? 'Sign in to your workspace'
            : boot.mode === 'live'
              ? 'Configured organization'
              : 'Demo organization · Northstar'}
        </span>
      </footer>
    </main>
  );
}
