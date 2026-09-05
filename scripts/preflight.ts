import 'dotenv/config';
import { connector } from '../server/connector';
import { embed, extractLive, modelSettings } from '../server/model';
import { INTAKE_PROMPT_VERSION } from '../server/intake-prompt';
import { pool, mode } from '../server/db';
try {
  await pool.query('SELECT 1');
  const info = await (await connector()).discover();
  console.log(JSON.stringify({ mode: mode(), jira: info }, null, 2));
  if (mode() === 'live') {
    const result = await extractLive('Corporate VPN cannot connect.', [
      'preflight-message',
    ]);
    const vector = await embed('VPN connectivity');
    console.log(
      JSON.stringify({
        model: process.env.OPENAI_MODEL,
        reasoningEffort: modelSettings().effort ?? 'model default',
        promptVersion: INTAKE_PROMPT_VERSION,
        structuredOutput: !!result.data,
        embeddingDimensions: vector.length,
        usage: result.usage,
      }),
    );
  } else
    console.log('Demo preflight passed. Live integration remains unverified.');
} finally {
  await pool.end();
}
