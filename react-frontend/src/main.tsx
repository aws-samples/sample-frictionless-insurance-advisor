import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

// Order matters: import Amplify configuration before anything that uses it.
import './amplify-config';
// i18n bootstraps language detection + Amplify Authenticator vocabulary wiring.
// Must run before App renders so the first paint is in the correct locale.
import './i18n/config';

import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import './index.css';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found');
}

createRoot(rootElement).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
);
