import { lazy, Suspense, useEffect, useState } from 'react';

import { AuthGate } from './components/AuthGate';
import { TopNav } from './components/TopNav';
import { AdvisorProvider } from './hooks/useAdvisor';
import { CustomersProvider } from './hooks/useCustomers';
import { usePersistentBoolean } from './hooks/usePersistentBoolean';
import { FormSessionsProvider } from './hooks/useApplicationForm';
import { RecommendationProvider } from './hooks/useRecommendation';
import { ThemeProvider } from './hooks/useTheme';
import { prefetchComparatorData } from './lib/api';
import { readDataDeepLink } from './lib/sources';
import { Spinner } from './ui';
import type { Page } from './types';

// Lazy-load each page so the initial bundle stays small. Auth + nav + the
// providers ship in the main chunk; the 3 pages are split out and fetched
// only when the user clicks into them. The chat and voice pages are the
// largest individually (Markdown + WebSocket / audio worklets respectively).
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
 * (using our custom sign-up flow that calls a backend Lambda — required
 * because the user pool is configured with AllowAdminCreateUserOnly,
 * which blocks Cognito self-service signup). Once authenticated,
 * AuthedShell renders the chosen page.
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
  const [page, setPage] = useState<Page>(deepLink ? 'data' : 'assistant');
  // Warm the Comparator's lists in the background while the advisor starts work.
  useEffect(() => {
    const id = window.setTimeout(prefetchComparatorData, 1500);
    return () => window.clearTimeout(id);
  }, []);
  // Bumped by the logo: remounts the Assistant page so it starts on Home again.
  const [homeKey, setHomeKey] = useState(0);
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
        <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
          <TopNav
            activePage={page}
            onChangePage={(next) => {
              setDeepLink(null); // the link only applies to the first view
              setPage(next);
            }}
            onHome={() => {
              setDeepLink(null);
              setPage('assistant');
              setHomeKey((k) => k + 1);
            }}
            advisorEmail={advisorEmail}
            onSignOut={onSignOut}
          />

          <Suspense fallback={<PageLoading />}>
            {page === 'assistant' ? (
              <AssistantPage key={homeKey} sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
            ) : null}
            {page === 'voice' ? (
              <VoicePage sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
            ) : null}
            {page === 'comparator' ? <ComparatorPage /> : null}
            {page === 'data' ? <DataPage initialFile={deepLink} /> : null}
          </Suspense>
        </div>
      </FormSessionsProvider>
      </RecommendationProvider>
    </CustomersProvider>
    </AdvisorProvider>
  );
}
