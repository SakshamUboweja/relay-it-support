import type { Pipeline } from '@/lib/domain';
import { pipelineLabel } from '@/lib/confidence';

export function PipelineChip({
  pipeline,
}: {
  pipeline: Pipeline | null | undefined;
}) {
  const label = pipelineLabel(pipeline);
  if (!label) return null;
  return <span className="pipeline-chip">{label}</span>;
}
