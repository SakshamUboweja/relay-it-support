import OpenAI from 'openai';
import { zodTextFormat } from 'openai/helpers/zod';
import { z } from 'zod';
import { services } from './domain';
import { policy } from './policy';
import { INTAKE_PROMPT } from './intake-prompt';
export const Extraction = z.object({
  summary: z.string().min(1).max(120),
  service: z.enum(services).nullable(),
  serviceQuote: z.string().nullable(),
  symptomQuote: z.string().nullable(),
  impactQuote: z.string().nullable(),
  urgencyQuote: z.string().nullable(),
  deviceQuote: z.string().nullable(),
  startedQuote: z.string().nullable(),
  workaroundQuote: z.string().nullable(),
  attemptedStepsQuotes: z.array(z.string()),
  supportRequestQuote: z.string().nullable(),
  procedureAttemptedQuote: z.string().nullable(),
  securityQuote: z
    .string()
    .nullable()
    .describe(
      'Exact evidence of suspected compromise, phishing, unauthorized access, unexpected MFA, malware, or data exposure. Null for ordinary login failures, user-initiated password changes, or routine access requests without a threat indicator.',
    ),
  evidenceIds: z.array(z.string()),
});
export function modelSettings() {
  const effort = z
    .enum(['none', 'low', 'medium', 'high', 'xhigh', 'max'])
    .optional()
    .parse(process.env.OPENAI_REASONING_EFFORT || undefined);
  const maxOutputTokens = z.coerce
    .number()
    .int()
    .min(512)
    .max(16384)
    .parse(process.env.OPENAI_MAX_OUTPUT_TOKENS || policy.maxOutputTokens);
  return { effort, maxOutputTokens };
}
export function liveClient() {
  if (
    !process.env.OPENAI_API_KEY ||
    !process.env.OPENAI_MODEL ||
    !process.env.OPENAI_EMBEDDING_MODEL
  )
    throw new Error(
      'Live mode requires OPENAI_API_KEY, OPENAI_MODEL and OPENAI_EMBEDDING_MODEL.',
    );
  return new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
    maxRetries: 0,
    timeout: 20000,
  });
}
export function validateExtraction(
  data: z.infer<typeof Extraction>,
  text: string,
  allowedIds: string[],
) {
  for (const quote of [
    data.serviceQuote,
    data.symptomQuote,
    data.impactQuote,
    data.urgencyQuote,
    data.securityQuote,
    data.deviceQuote,
    data.startedQuote,
    data.workaroundQuote,
    data.supportRequestQuote,
    data.procedureAttemptedQuote,
    ...data.attemptedStepsQuotes,
  ])
    if (quote && !text.includes(quote))
      throw new Error('Model output asserted unsupported evidence.');
  if (data.evidenceIds.some((id) => !allowedIds.includes(id)))
    throw new Error('Model output referenced disallowed evidence.');
  if (data.service && !data.serviceQuote)
    throw new Error('Service requires a source quote.');
  if (!data.evidenceIds.length)
    throw new Error('Extracted facts require message evidence IDs.');
  return data;
}
export async function extractLive(
  text: string,
  sourceIds: string[],
  approvedProcedure: { id: string; body: string } | null = null,
) {
  const client = liveClient();
  const settings = modelSettings();
  let last: unknown;
  for (let i = 0; i <= policy.maxModelRetries; i++) {
    try {
      const r = await client.responses.parse(
        {
          model: process.env.OPENAI_MODEL!,
          store: false,
          max_output_tokens: settings.maxOutputTokens,
          ...(settings.effort
            ? { reasoning: { effort: settings.effort } }
            : {}),
          input: [
            {
              role: 'system',
              content: INTAKE_PROMPT,
            },
            {
              role: 'user',
              content: JSON.stringify({
                message: text,
                evidenceIds: sourceIds,
                approvedProcedure,
              }),
            },
          ],
          text: { format: zodTextFormat(Extraction, 'intake') },
        },
        { timeout: 60000 },
      );
      if (r.status !== 'completed' || !r.output_parsed)
        throw new Error(
          'Model response incomplete or refused; saved for general intake.',
        );
      return {
        data: validateExtraction(r.output_parsed, text, sourceIds),
        usage: {
          input: r.usage?.input_tokens ?? 0,
          output: r.usage?.output_tokens ?? 0,
        },
      };
    } catch (e) {
      last = e;
      if (
        e instanceof OpenAI.APIError &&
        e.status &&
        e.status < 500 &&
        e.status !== 429
      )
        break;
    }
  }
  throw last;
}
export async function embed(text: string) {
  const r = await liveClient().embeddings.create({
    model: process.env.OPENAI_EMBEDDING_MODEL!,
    input: text.slice(0, 14000),
    dimensions: 256,
  });
  const values = r.data[0]?.embedding;
  if (values?.length !== 256 || values.some((x) => !Number.isFinite(x)))
    throw new Error('Embedding model must support 256 dimensions.');
  return values;
}
