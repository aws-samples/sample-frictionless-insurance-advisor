import type { ChatSource } from '../types';

const FOLDERS = new Set([
  'portfolio', 'company', 'competitive', 'competitors', 'promotion', 'forms', 'pricing',
]);

/** Only plain http(s) links are ever rendered as links. */
export function safeHttpUrl(url: unknown): string | null {
  if (typeof url !== 'string' || url.length > 2000) return null;
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.toString() : null;
  } catch {
    return null;
  }
}

/**
 * Validate the agent's `sources` event. It comes over the wire, so each entry
 * is checked: unknown folders, odd keys and non-http links are dropped.
 */
export function parseSources(raw: unknown): ChatSource[] {
  if (!Array.isArray(raw)) return [];
  const out: ChatSource[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const r = item as Record<string, unknown>;
    const title = typeof r.title === 'string' ? r.title.slice(0, 200) : '';
    if (r.kind === 'file' && typeof r.folder === 'string' && typeof r.key === 'string') {
      if (FOLDERS.has(r.folder) && /^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(r.key) && !r.key.includes('..')) {
        out.push({ kind: 'file', folder: r.folder, key: r.key, title: title || r.key });
      }
    } else if (r.kind === 'web') {
      const url = safeHttpUrl(r.url);
      if (url) out.push({ kind: 'web', url, title: title || new URL(url).hostname });
    }
  }
  return out.slice(0, 8);
}

/** Link that opens a document in Data Explorer, in a new browser tab. */
export function dataTabHref(folder: string, key: string): string {
  const params = new URLSearchParams({ page: 'data', folder, key });
  return `${window.location.pathname}?${params.toString()}`;
}

/** The document a `?page=data&folder=…&key=…` link points at, if any. */
export function readDataDeepLink(): { folder: string; key: string } | null {
  const q = new URLSearchParams(window.location.search);
  if (q.get('page') !== 'data') return null;
  const folder = q.get('folder');
  const key = q.get('key');
  if (!folder || !key || !FOLDERS.has(folder) || !/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(key) || key.includes('..')) {
    return null;
  }
  return { folder, key };
}

/** The illustration sentence the model may put right after the Sources line. */
const ILLUSTRATION_RE = /^\s*[*_>\s]*this is an illustration/i;

/**
 * Remove the model's "Sources: …" line. The footer replaces it, so the answer
 * does not list its sources twice. The line is looked for in the last few
 * lines of the answer (the model sometimes puts the illustration sentence
 * after it). Only a line that starts with "Sources:" (optionally wrapped in
 * markdown emphasis) is cut, together with the lines that continue it up to
 * the next blank line; a following illustration sentence is kept.
 */
export function stripSourcesLine(content: string): string {
  const lines = content.replace(/\s+$/, '').split('\n');
  const from = Math.max(0, lines.length - 8);
  for (let i = lines.length - 1; i >= from; i--) {
    if (!/^\s*[*_>\s-]*sources?\s*:/i.test(lines[i])) continue;
    let end = i + 1;
    while (end < lines.length && lines[end].trim() !== '' && !ILLUSTRATION_RE.test(lines[end])) end++;
    const kept = [...lines.slice(0, i), ...lines.slice(end)];
    return kept.join('\n').replace(/\n{3,}/g, '\n\n').replace(/\s+$/, '');
  }
  let p = lines.length - 1;
  while (p >= 0 && lines[p].trim() !== '') p--;
  // "**Sources:**" on its own, then a list in the final paragraph.
  const isList = lines.slice(p + 1).every((l) => /^\s*([-*+]|\d+[.)])\s/.test(l));
  if (isList && p >= 0) {
    let q = p - 1;
    while (q >= 0 && lines[q].trim() !== '') q--;
    const heading = lines.slice(q + 1, p);
    if (heading.length === 1 && /^[\s*_#>]*sources?\s*:?[\s*_]*$/i.test(heading[0])) {
      return lines.slice(0, q + 1).join('\n').replace(/\s+$/, '');
    }
  }
  return content;
}
