import { AlertCircle, CircleHelp, ShieldCheck } from 'lucide-react';
import type { Confidence } from '@/lib/domain';
import { confidenceCopy, isConfidence } from '@/lib/confidence';

const icons = {
  high: ShieldCheck,
  medium: CircleHelp,
  low: AlertCircle,
} as const;

export function ConfidenceBadge({
  confidence,
  showScore = false,
}: {
  confidence: Confidence | null | undefined;
  showScore?: boolean;
}) {
  if (!isConfidence(confidence)) return null;
  const Icon = icons[confidence.band];
  return (
    <span className={'confidence-badge ' + confidence.band}>
      <Icon size={13} aria-hidden="true" />
      {confidenceCopy(confidence.band).label}
      {showScore && ' · ' + confidence.value.toFixed(2)}
    </span>
  );
}
