import { lazy, Suspense, useCallback, useEffect, useState } from 'react';

import { AuthGate } from './components/AuthGate';
import { TopNav } from './components/TopNav';
import { AdvisorProvider } from './hooks/useAdvisor';
import { CustomersProvider } from './hooks/useCustomers';
import { usePersistentBoolean } from './hooks/usePersistentBoolean';
import { FormSessionsProvider } from './hooks/useApplicationForm';
import { RecommendationProvider } from './hooks/useRecommendation';
import { SelectionProvider, useSelectionState } from './hooks/useSelection';
import { ThemeProvider } from './hooks/useTheme';
import { prefetchComparatorData } from './lib/api';
import { readDataDeepLink } from './lib/sources';
import { Spinner } from './ui';
import type { Page } from './types';

// Lazy-load each page so the initial bundle stays small. Auth + nav + the
// providers ship in the main chunk; the pages are split out and fetched
// only when the user clicks into them. The chat and voice pages are the
// largest individually (Markdown + WebSocket / audio worklets respectively).
const HomePage = lazy(() =>
  import('./components/HomePage').then((m) => ({ default: m.HomePage }))
);
const AssistantPage = lazy(() =>
  import('./components/AssistantPage').then((m) => ({ default: m.AssistantPage }))
);
const VoicePage = lazy(() =>
  import('./components/VoicePage').then((m) => ({ default: m.VoicePage }))
);
const ComparatorPage = lazy(() =>
  import('./components/ComparatorPage').then((m) => ({ default: m.ComparatorPage }))
);
const DataPage = lazy(() =>
  import('./components/DataPage').then((m) => ({ default: m.DataPage }))
);

/** Centered spinner shown while a page chunk is being fetched. */
function PageLoading() {
  return (
    <div className="flex flex-1 items-center justify-center">
      <Spinner size="md" />
    </div>
  );
}

/**
 * Single-page app with a top-nav page switcher. AuthGate enforces sign-in
 * (and, when the backend deploys the opt-in sign-up Lambda, offers a custom
 * sign-up tab — needed because the user pool is configured with
 * AllowAdminCreateUserOnly, which blocks Cognito self-service signup).
 * Once authenticated, AuthedShell renders the chosen page.
 */
export default function App() {
  return (
    <ThemeProvider>
      <AuthGate>
        {({ advisorEmail, onSignOut }) => (
          <AuthedShell
            advisorEmail={advisorEmail}
            onSignOut={() => {
              void onSignOut();
            }}
          />
        )}
      </AuthGate>
    </ThemeProvider>
  );
}

interface AuthedShellProps {
  advisorEmail: string | null;
  onSignOut: () => void;
}

function AuthedShell({ advisorEmail, onSignOut }: AuthedShellProps) {
  // A Sources link in the chat opens this app in a new tab on the Data page
  // with one document selected. Read it once, then tidy the address bar.
  const [deepLink, setDeepLink] = useState(() => readDataDeepLink());
  const [page, setPage] = useState<Page>(deepLink ? 'data' : 'home');
  // The Text Assistant stays mounted (hidden) once it has been visited, so its on-screen chat and
  // a response that is still streaming survive a visit to another tab. Not mounted before the first visit.
  const [textVisited, setTextVisited] = useState(page === 'assistant');
  if (page === 'assistant' && !textVisited) setTextVisited(true);
  // Warm the Comparator's lists in the background while the advisor starts work.
  useEffect(() => {
    const id = window.setTimeout(prefetchComparatorData, 1500);
    return () => window.clearTimeout(id);
  }, []);
  // The Text Assistant tab: where a customer chosen on Home (or a Cockpit button) continues.
  const openText = useCallback(() => {
    setDeepLink(null);
    setPage('assistant');
  }, []);
  // The customer being worked on, shared by the Home, Text and Voice pages.
  // A Cockpit button always continues in the Text Assistant.
  const selection = useSelectionState(openText);
  // Home, used by both the Home tab and the logo: show the Cockpit with no customer and no prospect, and drop a
  // pending deep link. The Text page is not remounted, so its chats and unsent drafts are kept.
  const goHome = () => {
    setDeepLink(null);
    setPage('home');
    selection.home();
  };
  useEffect(() => {
    if (deepLink) window.history.replaceState(null, '', window.location.pathname);
  }, [deepLink]);
  // Shared sidebar collapse state persisted across page switches and reloads.
  const [sidebarCollapsed, , toggleSidebar] = usePersistentBoolean(
    'insadv.sidebarCollapsed',
    false
  );

  return (
    <AdvisorProvider advisorEmail={advisorEmail}>
    <CustomersProvider>
      <RecommendationProvider>
      <FormSessionsProvider>
      <SelectionProvider value={selection}>
        <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
          <TopNav
            activePage={page}
            onChangePage={(next) => {
              if (next === 'home') {
                goHome();
                return;
              }
              setDeepLink(null); // the link only applies to the first view
              setPage(next);
            }}
            onHome={goHome}
            advisorEmail={advisorEmail}
            onSignOut={onSignOut}
          />

          {textVisited ? (
            // `contents` keeps the page root a direct flex item of the shell while shown. While another
            // tab is active the wrapper is display:none and inert, so nothing in it can be focused,
            // clicked or read by assistive technology. It has its own Suspense boundary so a lazy page
            // loading elsewhere never hides and re-shows it.
            <div
              className={page === 'assistant' ? 'contents' : 'hidden'}
              hidden={page !== 'assistant'}
              aria-hidden={page !== 'assistant' ? true : undefined}
              {...(page !== 'assistant' ? { inert: '' } : {})}
            >
              <Suspense fallback={<PageLoading />}>
                <AssistantPage
                  active={page === 'assistant'}
                  sidebarCollapsed={sidebarCollapsed}
                  onToggleSidebar={toggleSidebar}
                />
              </Suspense>
            </div>
          ) : null}
          <Suspense fallback={<PageLoading />}>
            {page === 'home' ? (
              <HomePage
                sidebarCollapsed={sidebarCollapsed}
                onToggleSidebar={toggleSidebar}
                onOpenText={openText}
              />
            ) : null}
            {page === 'voice' ? (
              <VoicePage sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
            ) : null}
            {page === 'comparator' ? <ComparatorPage /> : null}
            {page === 'data' ? <DataPage initialFile={deepLink} /> : null}
          </Suspense>
        </div>
      </SelectionProvider>
      </FormSessionsProvider>
      </RecommendationProvider>
    </CustomersProvider>
    </AdvisorProvider>
  );
}
