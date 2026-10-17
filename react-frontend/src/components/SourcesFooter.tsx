import { ExternalLink, FileText, Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { dataTabHref, safeHttpUrl } from '../lib/sources';
import type { ChatSource } from '../types';

/**
 * Sources under an assistant answer. Documents open in Data Explorer and web
 * pages open on their own site, both in a new browser tab so the conversation
 * stays where it is (the chat lives on the Assistant page and would be lost
 * by switching tabs inside the app).
 */
export function SourcesFooter({ sources }: { sources: ChatSource[] }) {
  const { t } = useTranslation();
  if (sources.length === 0) return null;
  return (
    <div className="mt-2.5 border-t border-border pt-2">
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-foreground-muted">
        {t('assistant.chat.sources.label')}
      </div>
      <ul className="flex flex-wrap gap-1.5">
        {sources.map((s) => {
          const isFile = s.kind === 'file';
          const href = isFile ? dataTabHref(s.folder, s.key) : safeHttpUrl(s.url);
          if (!href) return null;
          const host = !isFile ? new URL(href).hostname.replace(/^www\./, '') : null;
          return (
            <li key={isFile ? `${s.folder}/${s.key}` : s.url} className="max-w-full">
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                title={t(isFile ? 'assistant.chat.sources.openFile' : 'assistant.chat.sources.openWeb', { title: s.title })}
                className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-background-muted/60 px-2.5 py-1 text-xs text-foreground hover:border-brand-2 hover:bg-background-muted"
              >
                {isFile ? (
                  <FileText className="h-3.5 w-3.5 shrink-0 text-brand-2" aria-hidden />
                ) : (
                  <Globe className="h-3.5 w-3.5 shrink-0 text-brand-1" aria-hidden />
                )}
                <span className="truncate">{s.title}</span>
                {host ? <span className="shrink-0 text-[10px] text-foreground-muted">{host}</span> : null}
                <ExternalLink className="h-3 w-3 shrink-0 text-foreground-muted" aria-hidden />
              </a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
