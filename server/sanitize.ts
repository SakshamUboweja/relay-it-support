/** Best-effort known-pattern sanitation, not a universal DLP classifier. */
export function sanitize(text: string) {
  return text
    .replace(/\bsk-[A-Za-z0-9_-]{12,}\b/g, '[REDACTED API KEY]')
    .replace(
      /\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]+)\b/g,
      '[REDACTED TOKEN]',
    )
    .replace(
      /\b(password|passwd|api[ _-]?key|access[ _-]?token|authorization|recovery[ _-]?code)\s*(?:is\s+|[:=]\s*)[^\s,;]+/gi,
      '$1: [REDACTED]',
    )
    .replace(
      /\b(MFA|verification|one.time|OTP|backup)\s*(?:code)?\s*(?:is|:|=)?\s*\d{6,8}\b/gi,
      '$1 code [REDACTED]',
    )
    .replace(/Bearer\s+[A-Za-z0-9._~-]+/gi, 'Bearer [REDACTED]');
}
