import { lazy, Suspense, useState } from 'react';

import { AuthGate } from './components/AuthGate';
import { TopNav } from './components/TopNav';
import { CustomersProvider } from './hooks/useCustomers';
import { usePersistentBoolean } from './hooks/usePersistentBoolean';
import { RecommendationProvider } from './hooks/useRecommendation';
import { ThemeProvider } from './hooks/useTheme';
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
 * because the org policy in this account blocks Cognito self-service
 * signup). Once authenticated, AuthedShell renders the chosen page.
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
  const [page, setPage] = useState<Page>('assistant');
  // Shared sidebar collapse state persisted across page switches and reloads.
  const [sidebarCollapsed, , toggleSidebar] = usePersistentBoolean(
    'insadv.sidebarCollapsed',
    false
  );

  return (
    <CustomersProvider>
      <RecommendationProvider>
        <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
          <TopNav
            activePage={page}
            onChangePage={setPage}
            advisorEmail={advisorEmail}
            onSignOut={onSignOut}
          />

          <Suspense fallback={<PageLoading />}>
            {page === 'assistant' ? (
              <AssistantPage sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
            ) : null}
            {page === 'voice' ? (
              <VoicePage sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
            ) : null}
            {page === 'comparator' ? <ComparatorPage /> : null}
          </Suspense>
        </div>
      </RecommendationProvider>
    </CustomersProvider>
  );
}
