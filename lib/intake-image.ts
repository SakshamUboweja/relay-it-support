/** Client-side rules for the one screenshot a chat message may carry.
 *
 * These mirror the intake endpoint so a bad file is refused before it is
 * uploaded; the server still enforces the same limits.
 */
export const IMAGE_TYPES = ['image/png', 'image/jpeg'] as const;
export const IMAGE_MAX_BYTES = 5 * 1024 * 1024;

/** The reason this file cannot be attached, or null when it can. */
export function validateImage(file: {
  type: string;
  size: number;
  name: string;
}): string | null {
  if (!IMAGE_TYPES.includes(file.type as (typeof IMAGE_TYPES)[number]))
    return 'Choose a PNG or JPEG image.';
  if (file.size > IMAGE_MAX_BYTES) return 'Images must be 5 MB or smaller.';
  if (file.size <= 0) return 'That image is empty.';
  return null;
}

/** Identifies a chosen file so a retry of the same send stays idempotent. */
export function imageSignature(file: {
  name: string;
  size: number;
  lastModified: number;
  type: string;
}): string {
  return [file.name, file.size, file.lastModified, file.type].join('|');
}

// Whole units print without a decimal; everything else keeps one.
const scale = (bytes: number, unit: number) =>
  bytes % unit === 0 ? String(bytes / unit) : (bytes / unit).toFixed(1);

/** A short "412 KB · PNG" line for the composer preview. */
export function describeImage(file: { size: number; type: string }): string {
  const size =
    file.size < 1024 * 1024
      ? `${scale(file.size, 1024)} KB`
      : `${scale(file.size, 1024 * 1024)} MB`;
  const kind = file.type.split('/')[1]?.toUpperCase() || 'IMAGE';
  return `${size} · ${kind}`;
}
