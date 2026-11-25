import { memo } from 'react';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import './MarkdownMessage.css';

interface MarkdownMessageProps {
  content: string;
  /**
   * `chat` (default) uses the compressed scale suited to narrow chat bubbles.
   * `doc` restores a full document scale — visible heading hierarchy and
   * looser rhythm — for rendering standalone documents (the Data explorer).
   */
  variant?: 'chat' | 'doc';
}

/**
 * Renders agent-generated Markdown inside a chat bubble.
 *
 * - react-markdown does NOT allow raw HTML by default, which is the safe
 *   default. We do not add `rehype-raw`; anything that looks like HTML gets
 *   escaped and displayed as literal text.
 * - `remark-gfm` adds GitHub-flavoured Markdown: tables, task lists,
 *   strikethrough, and autolinks. Claude uses these regularly.
 * - Memoized on content because parent chat panels re-render every streamed
 *   token; re-parsing the same string is wasted work.
 * - Links open in a new tab with `noopener noreferrer` for safety.
 * - Images are never rendered. The browser fetches an image URL as soon as the
 *   message appears, so a URL that carries data would leave the page without a
 *   click. Markdown image syntax shows its alt text as plain text instead.
 *
 * Styling lives in MarkdownMessage.css so the component stays focused on
 * behavior.
 */
function MarkdownMessageInner({ content, variant = 'chat' }: MarkdownMessageProps) {
  return (
    <div className={variant === 'doc' ? 'markdown-message markdown-doc' : 'markdown-message'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => (
            // nosemgrep: react-props-spreading -- typed forwarding via AnchorHTMLAttributes
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          img: ({ alt }) => (alt ? <span>{alt}</span> : null),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownMessage = memo(MarkdownMessageInner);
