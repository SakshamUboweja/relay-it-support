'use client';
import { useRef, useState } from 'react';
import { ArrowUp, ImagePlus, RefreshCw, ShieldCheck, X } from 'lucide-react';
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
} from '@/components/ui/attachment';
import { describeImage, validateImage } from '@/lib/intake-image';

/** A chosen screenshot and the object URL that previews it. */
export type ComposerImage = { file: File; url: string };

/** The chat composer: message text plus at most one screenshot.
 *
 * The text lives in the page so tools and suggestion buttons can set it; the
 * image is owned by the page too, because it has to survive a failed send and
 * be revoked when the conversation is replaced.
 */
export function Composer({
  id,
  label,
  placeholder,
  value,
  onChange,
  image,
  onImageChange,
  onError,
  busy,
  onSubmit,
}: {
  id: string;
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  image: ComposerImage | null;
  onImageChange: (next: ComposerImage | null) => void;
  onError: (message: string) => void;
  busy: boolean;
  onSubmit: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  /** Validates a candidate file and reports whether it was kept. */
  function pick(file: File): boolean {
    const problem = validateImage(file);
    if (problem) {
      onError(problem);
      return false;
    }
    onImageChange({ file, url: URL.createObjectURL(file) });
    return true;
  }

  return (
    // Dropping a file is an enhancement; the attach button is the keyboard path.
    // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <form
      className={'composer' + (dragging ? ' dragging' : '')}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!busy) setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node))
          setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (busy) return;
        const files = [...e.dataTransfer.files];
        if (!files.length) return;
        const first =
          files.find((f) => f.type.startsWith('image/')) ?? files[0];
        if (pick(first) && files.length > 1)
          onError('One image per message; the first image was kept.');
      }}
    >
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        value={value}
        maxLength={6000}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            onSubmit();
          }
        }}
        onPaste={(e) => {
          if (busy) return;
          const item = [...e.clipboardData.items].find(
            (entry) => entry.kind === 'file' && entry.type.startsWith('image/'),
          );
          const blob = item?.getAsFile();
          if (!blob) return;
          // Only swallow the paste once a screenshot is in hand, so pasting
          // text keeps working.
          e.preventDefault();
          pick(
            new File([blob], `screenshot-${Date.now()}.png`, {
              type: blob.type,
            }),
          );
        }}
      />
      {image && (
        <div className="composer-attachments">
          <Attachment className="min-w-0" state={busy ? 'uploading' : 'done'}>
            <AttachmentMedia variant="image">
              {/* A blob URL cannot be optimised, and the export has no loader. */}
              {/* oxlint-disable-next-line no-img-element */}
              <img src={image.url} alt="" />
            </AttachmentMedia>
            <AttachmentContent>
              <AttachmentTitle>{image.file.name}</AttachmentTitle>
              <AttachmentDescription>
                {describeImage(image.file)}
              </AttachmentDescription>
            </AttachmentContent>
            <AttachmentActions>
              <AttachmentAction
                type="button"
                aria-label="Remove image"
                disabled={busy}
                onClick={() => onImageChange(null)}
              >
                <X size={14} />
              </AttachmentAction>
            </AttachmentActions>
          </Attachment>
        </div>
      )}
      <div className="composer-bottom">
        <span>
          <ShieldCheck size={15} />
          Your report is saved privately
        </span>
        <input
          ref={fileInput}
          type="file"
          className="sr-only"
          tabIndex={-1}
          aria-label="Choose a screenshot"
          accept="image/png,image/jpeg"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) pick(file);
          }}
        />
        <button
          type="button"
          className="composer-attach"
          aria-label="Add a screenshot"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          <ImagePlus size={18} />
        </button>
        <button
          className="send"
          disabled={busy || !value.trim()}
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
  );
}
