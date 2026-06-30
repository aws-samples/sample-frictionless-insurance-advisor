import { ChangeEvent, useRef } from 'react';

import { FileText, Image as ImageIcon, Paperclip, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '../lib/cn';
import { IconButton, Spinner } from '../ui';
import type { UploadedDocument } from '../types';

interface DocumentAttachmentProps {
  attachment: UploadedDocument | null;
  onAttach: (file: File) => void;
  onRemove: () => void;
  disabled?: boolean;
}

const ACCEPTED_FILE_TYPES = '.pdf,.md,.txt,.jpg,.jpeg,.png,.webp,application/pdf,text/markdown,text/plain,image/jpeg,image/png,image/webp';

/**
 * 📎 paperclip button + chip showing the currently-attached document.
 * Used by both ChatPanel and VoicePanel.
 *
 * - When no file is attached: renders just the paperclip button.
 * - When uploading: chip shows a spinner and the filename.
 * - When ready: chip shows the file icon, filename, and an X to remove.
 * - On error: chip shows the error message in red, dismissable via X.
 */
export function DocumentAttachment({
  attachment,
  onAttach,
  onRemove,
  disabled,
}: DocumentAttachmentProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      onAttach(file);
    }
    // Allow selecting the same file again later — clear the input value.
    e.target.value = '';
  };

  const FileIcon =
    attachment?.content_type.startsWith('image/') ? ImageIcon : FileText;

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_FILE_TYPES}
        className="hidden"
        onChange={handleFileChange}
      />
      <IconButton
        type="button"
        aria-label={t('assistant.chat.attachDocument')}
        title={t('assistant.chat.attachDocument')}
        variant="ghost"
        size="md"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || attachment?.status === 'uploading'}
      >
        <Paperclip className="h-4 w-4" />
      </IconButton>

      {attachment ? (
        <div
          className={cn(
            'flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs',
            attachment.status === 'error'
              ? 'border-destructive/40 bg-destructive/5 text-destructive'
              : 'border-border bg-background-elevated text-foreground'
          )}
        >
          {attachment.status === 'uploading' ? (
            <Spinner size="sm" />
          ) : (
            <FileIcon className="h-3.5 w-3.5 shrink-0 text-brand-2" />
          )}
          <span className="max-w-[12rem] truncate" title={attachment.filename}>
            {attachment.status === 'uploading'
              ? t('assistant.chat.uploadingFilename', { filename: attachment.filename })
              : attachment.status === 'error'
                ? attachment.error || t('assistant.chat.uploadFailed')
                : attachment.filename}
          </span>
          {attachment.status !== 'uploading' ? (
            <button
              type="button"
              aria-label={t('assistant.chat.removeAttachment')}
              onClick={onRemove}
              className="rounded text-foreground-muted hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
