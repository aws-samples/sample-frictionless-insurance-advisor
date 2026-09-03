import { useEffect, useMemo, useState } from 'react';

import {
  Database,
  FileCode2,
  FileText,
  FolderOpen,
  Search,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { listDataFiles, loadDataFile } from '../lib/api';
import { cn } from '../lib/cn';
import { fmtDate } from '../lib/format';
import { Alert, Badge, Card, IconButton, Spinner } from '../ui';
import type {
  DataFile,
  DataFileContent,
  DataFolderGroup,
  FormSchema,
} from '../types';

import { MarkdownMessage } from './MarkdownMessage';
import { SplitWorkspace } from './SplitWorkspace';

/**
 * Folders whose content is internal-only sales material (battlecards and
 * competitor analysis). Flagged in the viewer so an advisor doesn't screen-
 * share it to a customer — same intent as the comparator's BR-COMP-002 badge.
 */
const INTERNAL_FOLDERS = new Set(['competitive', 'competitors']);

/** Stable identity for a document across the two panes. */
function fileId(file: Pick<DataFile, 'folder' | 'key'>): string {
  return `${file.folder}/${file.key}`;
}

/**
 * Best-effort parse of a JSON document into a FormSchema.
 *
 * Returns null when the payload isn't a form schema, so the viewer falls back
 * to raw source rather than rendering a half-empty form. Only the fields the
 * renderer actually reads are required here — everything else is optional in
 * the type and handled defensively at render time.
 */
function parseFormSchema(content: string): FormSchema | null {
  try {
    const parsed: unknown = JSON.parse(content);
    if (!parsed || typeof parsed !== 'object') return null;
    const candidate = parsed as Partial<FormSchema>;
    if (typeof candidate.title !== 'string') return null;
    if (!Array.isArray(candidate.sections)) return null;
    const sectionsLookRight = candidate.sections.every(
      (s) => s && typeof s.title === 'string' && Array.isArray(s.fields)
    );
    if (!sectionsLookRight) return null;
    return parsed as FormSchema;
  } catch {
    return null;
  }
}

/**
 * Read-only browser over the reference documents that ground the assistant's
 * answers (the s3-data corpus). Two panes: a searchable folder-grouped file
 * list, and a reading pane that renders markdown properly or pretty-prints
 * form JSON.
 *
 * The listing is fetched once on mount (metadata only, no bodies); document
 * content is fetched lazily per selection and cached for the session so
 * clicking back to a document you've already opened is instant.
 */
export function DataPage() {
  const { t } = useTranslation();

  // Listing state
  const [groups, setGroups] = useState<DataFolderGroup[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [listLoading, setListLoading] = useState<boolean>(true);
  const [listError, setListError] = useState<string | null>(null);

  // Selection + document state
  const [selected, setSelected] = useState<DataFile | null>(null);
  const [doc, setDoc] = useState<DataFileContent | null>(null);
  const [docLoading, setDocLoading] = useState<boolean>(false);
  const [docError, setDocError] = useState<string | null>(null);
  const [docCache, setDocCache] = useState<Record<string, DataFileContent>>({});

  const [query, setQuery] = useState<string>('');
  const [showRaw, setShowRaw] = useState<boolean>(false);

  // Load the file listing once.
  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    listDataFiles()
      .then((data) => {
        if (cancelled) return;
        setGroups(data.folders);
        setTotal(data.total);
        setListError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setListError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch the selected document, serving from cache when we've seen it.
  useEffect(() => {
    if (!selected) {
      setDoc(null);
      setDocError(null);
      return;
    }
    const id = fileId(selected);
    const cached = docCache[id];
    if (cached) {
      setDoc(cached);
      setDocError(null);
      setDocLoading(false);
      return;
    }
    let cancelled = false;
    setDocLoading(true);
    setDocError(null);
    loadDataFile(selected.folder, selected.key)
      .then((content) => {
        if (cancelled) return;
        setDoc(content);
        setDocCache((prev) => ({ ...prev, [id]: content }));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDocError(err instanceof Error ? err.message : String(err));
        setDoc(null);
      })
      .finally(() => {
        if (!cancelled) setDocLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // docCache is intentionally omitted: adding an entry must not retrigger
    // the fetch that produced it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // Markdown-only concern; JSON always renders as source.
  useEffect(() => {
    setShowRaw(false);
  }, [selected]);

  const folderLabel = (folder: string): string =>
    t(`assistant.pages.data.folders.${folder}`, { defaultValue: folder });

  // Case-insensitive filter over filename, sub-path, and folder label so
  // typing "bigrival" or "competitor" both narrow usefully.
  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((group) => ({
        ...group,
        files: group.files.filter((f) => {
          const haystack = `${f.name} ${f.path} ${group.folder} ${folderLabel(
            group.folder
          )}`.toLowerCase();
          return haystack.includes(q);
        }),
      }))
      .filter((group) => group.files.length > 0);
    // folderLabel closes over t, which is stable per language.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groups, query, t]);

  const matchCount = useMemo(
    () => filteredGroups.reduce((sum, g) => sum + g.files.length, 0),
    [filteredGroups]
  );

  const isFiltering = query.trim().length > 0;

  return (
    <div className="flex flex-1 flex-col overflow-hidden min-h-0">
      {/* Page header */}
      <div className="shrink-0 border-b border-border px-6 py-4 sm:px-8">
        <div className="flex items-start gap-3">
          <Database className="mt-0.5 h-6 w-6 shrink-0 text-brand-2" />
          <div className="min-w-0 flex-1">
            <h2 className="text-2xl font-bold tracking-tight">
              <span className="text-brand-gradient">
                {t('assistant.pages.data.heading')}
              </span>
            </h2>
            {/* No max-width cap here: this header spans the full window, so a
                `max-w-prose` (65ch) limit would strand the sentence on the
                left and wrap it over three lines on a wide display. */}
            <p className="mt-1 text-sm text-foreground-muted">
              {t('assistant.pages.data.intro')}
            </p>
          </div>
        </div>
      </div>

      {/* Panes */}
      <div className="min-h-0 flex-1">
        <SplitWorkspace
          // v2: the initial release shipped a mismatched panel budget that
          // rendered the list ~72% wide. Bumping the key discards layouts
          // persisted from that build so users get the corrected default.
          storageId="insadv.data.split.v2"
          defaultLeftSize={30}
          minLeftSize={20}
          maxLeftSize={45}
          left={
            <FileList
              groups={filteredGroups}
              total={total}
              matchCount={matchCount}
              isFiltering={isFiltering}
              query={query}
              onQueryChange={setQuery}
              loading={listLoading}
              error={listError}
              selectedId={selected ? fileId(selected) : null}
              onSelect={setSelected}
              folderLabel={folderLabel}
            />
          }
          right={
            <DocumentViewer
              file={selected}
              doc={doc}
              loading={docLoading}
              error={docError}
              showRaw={showRaw}
              onToggleRaw={setShowRaw}
              folderLabel={folderLabel}
            />
          }
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Left pane — search + folder-grouped file list
// ---------------------------------------------------------------------------

interface FileListProps {
  groups: DataFolderGroup[];
  total: number;
  matchCount: number;
  isFiltering: boolean;
  query: string;
  onQueryChange: (value: string) => void;
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  onSelect: (file: DataFile) => void;
  folderLabel: (folder: string) => string;
}

function FileList({
  groups,
  total,
  matchCount,
  isFiltering,
  query,
  onQueryChange,
  loading,
  error,
  selectedId,
  onSelect,
  folderLabel,
}: FileListProps) {
  const { t } = useTranslation();

  return (
    <div className="flex h-full flex-col border-r border-border bg-background-elevated/40">
      {/* Search */}
      <div className="shrink-0 border-b border-border p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-foreground-muted" />
          <input
            type="search"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={t('assistant.pages.data.searchPlaceholder')}
            aria-label={t('assistant.pages.data.searchPlaceholder')}
            className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-8 text-xs text-foreground transition-[border-color] placeholder:text-foreground-muted focus-visible:border-ring/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
          />
          {query ? (
            <span className="absolute right-1 top-1/2 -translate-y-1/2">
              <IconButton
                aria-label={t('assistant.pages.data.clearSearch')}
                variant="ghost"
                size="sm"
                onClick={() => onQueryChange('')}
              >
                <X className="h-3 w-3" />
              </IconButton>
            </span>
          ) : null}
        </div>
        {!loading && !error ? (
          <p className="mt-2 text-[11px] text-foreground-muted">
            {isFiltering
              ? t('assistant.pages.data.matchCount', { count: matchCount, total })
              : t('assistant.pages.data.fileCount', { count: total })}
          </p>
        ) : null}
      </div>

      {/* List */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading ? (
          <div className="inline-flex items-center gap-2 text-sm text-foreground-muted">
            <Spinner size="sm" />
            {t('assistant.pages.data.loadingList')}
          </div>
        ) : null}

        {error ? (
          <Alert variant="danger">
            {t('common.errors.loadFailed', { message: error })}
          </Alert>
        ) : null}

        {!loading && !error && groups.length === 0 ? (
          <p className="text-sm italic text-foreground-muted">
            {isFiltering
              ? t('assistant.pages.data.noMatches', { query })
              : t('assistant.pages.data.emptyList')}
          </p>
        ) : null}

        {!loading && !error && groups.length > 0 ? (
          <div className="flex flex-col gap-4">
            {groups.map((group) => (
              <section key={group.folder}>
                <header className="mb-1.5 flex items-center gap-1.5 px-1">
                  <FolderOpen className="h-3.5 w-3.5 shrink-0 text-brand-2" />
                  <h3 className="truncate text-xs font-semibold uppercase tracking-wide">
                    {folderLabel(group.folder)}
                  </h3>
                  <span className="ml-auto shrink-0 text-[11px] tabular-nums text-foreground-muted">
                    {group.files.length}
                  </span>
                </header>
                <ul className="flex flex-col gap-0.5">
                  {group.files.map((file) => {
                    const id = fileId(file);
                    const isSelected = id === selectedId;
                    const Icon = file.content_type === 'json' ? FileCode2 : FileText;
                    return (
                      <li key={id}>
                        <button
                          type="button"
                          onClick={() => onSelect(file)}
                          aria-current={isSelected ? 'true' : undefined}
                          className={cn(
                            'flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-[background,box-shadow]',
                            isSelected
                              ? 'bg-gradient-to-r from-brand-2/15 via-brand-1/10 to-transparent shadow-[var(--shadow-sm)] ring-1 ring-brand-2/30'
                              : 'hover:bg-background-muted'
                          )}
                        >
                          <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-foreground-muted" />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium text-foreground">
                              {file.name}
                            </span>
                            {file.path ? (
                              <span className="block truncate font-mono text-[10px] text-foreground-muted">
                                {file.path}/
                              </span>
                            ) : null}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right pane — document viewer
// ---------------------------------------------------------------------------

interface DocumentViewerProps {
  file: DataFile | null;
  doc: DataFileContent | null;
  loading: boolean;
  error: string | null;
  showRaw: boolean;
  onToggleRaw: (value: boolean) => void;
  folderLabel: (folder: string) => string;
}

function DocumentViewer({
  file,
  doc,
  loading,
  error,
  showRaw,
  onToggleRaw,
  folderLabel,
}: DocumentViewerProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="max-w-sm text-center text-sm italic text-foreground-muted">
          {t('assistant.pages.data.selectFile')}
        </p>
      </div>
    );
  }

  const isInternal = INTERNAL_FOLDERS.has(file.folder);
  const lastUpdated = doc?.metadata?.last_updated;
  const contentType = doc?.content_type ?? file.content_type;
  const isMarkdown = contentType === 'markdown';
  // Form-schema JSON gets a rendered view too. Anything else that happens to
  // be JSON falls back to source, so the toggle is only offered when there is
  // genuinely something to render.
  const schema =
    doc && contentType === 'json' ? parseFormSchema(doc.content) : null;
  const canRender = isMarkdown || schema !== null;

  return (
    <div className="flex h-full flex-col">
      {/* Document header */}
      <div className="shrink-0 border-b border-border bg-gradient-to-r from-brand-2/10 via-brand-1/5 to-transparent px-5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold">{file.name}</h3>
            <p className="mt-0.5 truncate font-mono text-[11px] text-foreground-muted">
              {file.folder}/{file.key}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {isInternal ? (
              <Badge variant="warning" className="uppercase tracking-wide">
                {t('assistant.pages.data.internalUseOnly')}
              </Badge>
            ) : null}
            <Badge variant="info">{folderLabel(file.folder)}</Badge>
            {canRender ? (
              <div className="inline-flex items-center gap-0.5 rounded-full border border-border bg-background-muted/60 p-0.5">
                <ViewToggleButton
                  active={!showRaw}
                  onClick={() => onToggleRaw(false)}
                  label={t('assistant.pages.data.viewRendered')}
                />
                <ViewToggleButton
                  active={showRaw}
                  onClick={() => onToggleRaw(true)}
                  label={t('assistant.pages.data.viewRaw')}
                />
              </div>
            ) : null}
          </div>
        </div>
        {lastUpdated ? (
          <p className="mt-1.5 text-[11px] text-foreground-muted">
            {t('assistant.pages.data.lastUpdated', {
              date: fmtDate(lastUpdated, locale),
            })}
          </p>
        ) : null}
      </div>

      {/* Document body. Padding is kept modest because the rendered-markdown
          Card supplies its own inset; doubling both wasted horizontal space. */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {loading ? (
          <div className="inline-flex items-center gap-2 text-sm text-foreground-muted">
            <Spinner size="sm" />
            {t('assistant.pages.data.loadingFile')}
          </div>
        ) : null}

        {error ? (
          <Alert variant="danger">
            {t('common.errors.loadFailed', { message: error })}
          </Alert>
        ) : null}

        {!loading && !error && doc ? (
          canRender && !showRaw ? (
            isMarkdown ? (
              <Card className="p-5 sm:p-7">
                <MarkdownMessage content={doc.content} variant="doc" />
              </Card>
            ) : (
              // schema is non-null whenever canRender is true and it isn't
              // markdown — see the canRender derivation above.
              <FormSchemaView schema={schema as FormSchema} />
            )
          ) : (
            <pre className="overflow-x-auto rounded-lg border border-border bg-background-muted/40 p-4 font-mono text-xs leading-relaxed text-foreground">
              {doc.content}
            </pre>
          )
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rendered view for application-form schemas
// ---------------------------------------------------------------------------

/**
 * Human-readable rendering of a form schema: a title block with counts, then
 * one card per section listing its fields with type, required state, hint and
 * select options. The schema is data the agent consumes, so the field `path`
 * stays visible (in mono) — it's the identifier the voice fill-in targets.
 */
function FormSchemaView({ schema }: { schema: FormSchema }) {
  const { t } = useTranslation();

  const sections = Array.isArray(schema.sections) ? schema.sections : [];
  const allFields = sections.flatMap((s) => (Array.isArray(s.fields) ? s.fields : []));
  const requiredCount = allFields.filter((f) => f.required).length;

  return (
    <div className="flex flex-col gap-4">
      {/* Title + summary */}
      <Card feature className="p-5">
        <h2 className="text-xl font-bold tracking-tight">
          <span className="text-brand-gradient">{schema.title}</span>
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-foreground-muted">
          {schema.product_name ? (
            <span>
              {t('assistant.pages.data.form.product')}:{' '}
              <span className="font-medium text-foreground">{schema.product_name}</span>
            </span>
          ) : null}
          {schema.form_id ? (
            <span>
              {t('assistant.pages.data.form.formId')}:{' '}
              <span className="font-mono text-foreground">{schema.form_id}</span>
            </span>
          ) : null}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Badge variant="neutral">
            {t('assistant.pages.data.form.sectionCount', { count: sections.length })}
          </Badge>
          <Badge variant="neutral">
            {t('assistant.pages.data.form.fieldCount', { count: allFields.length })}
          </Badge>
          <Badge variant="info">
            {t('assistant.pages.data.form.requiredCount', { count: requiredCount })}
          </Badge>
        </div>
      </Card>

      {/* Sections */}
      {sections.map((section, sectionIdx) => {
        const fields = Array.isArray(section.fields) ? section.fields : [];
        return (
          <Card key={section.id ?? `${section.title}-${sectionIdx}`} className="overflow-hidden">
            <header className="flex items-center gap-2 border-b border-border bg-background-muted/40 px-4 py-2.5">
              <span className="grid h-5 w-5 shrink-0 place-items-center rounded bg-brand-2/15 text-[11px] font-bold tabular-nums text-brand-2">
                {sectionIdx + 1}
              </span>
              <h3 className="text-sm font-semibold">{section.title}</h3>
              <span className="ml-auto text-[11px] tabular-nums text-foreground-muted">
                {fields.length}
              </span>
            </header>

            <ul className="divide-y divide-border">
              {fields.map((field, fieldIdx) => (
                <li key={field.path ?? `${section.id}-${fieldIdx}`} className="px-4 py-3">
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <span className="text-sm font-medium text-foreground">{field.label}</span>
                    {field.type ? (
                      <span className="rounded bg-background-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground-muted">
                        {field.type}
                      </span>
                    ) : null}
                    <span className="ml-auto shrink-0">
                      {field.required ? (
                        <Badge variant="warning">
                          {t('assistant.pages.data.form.required')}
                        </Badge>
                      ) : (
                        <Badge variant="neutral">
                          {t('assistant.pages.data.form.optional')}
                        </Badge>
                      )}
                    </span>
                  </div>

                  <p className="mt-1 font-mono text-[11px] text-foreground-muted">
                    {field.path}
                  </p>

                  {field.hint ? (
                    <p className="mt-1 text-xs italic text-foreground-muted">{field.hint}</p>
                  ) : null}

                  {Array.isArray(field.options) && field.options.length > 0 ? (
                    <div className="mt-2">
                      <span className="text-[10px] font-semibold uppercase tracking-wide text-foreground-muted">
                        {t('assistant.pages.data.form.options')}
                      </span>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {field.options.map((opt) => (
                          <span
                            key={opt.value}
                            title={opt.value}
                            className="rounded-full border border-border bg-background-elevated px-2 py-0.5 text-[11px] text-foreground"
                          >
                            {opt.label}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </Card>
        );
      })}
    </div>
  );
}

function ViewToggleButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'rounded-full px-2.5 py-1 text-[11px] font-medium transition-[background,color]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        active
          ? 'nav-pill-active'
          : 'text-foreground-muted hover:bg-background-elevated hover:text-foreground'
      )}
    >
      {label}
    </button>
  );
}
