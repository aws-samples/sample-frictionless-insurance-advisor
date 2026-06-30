import { Component, ErrorInfo, ReactNode } from 'react';

import i18n from '../i18n/config';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  message: string | null;
}

/**
 * App-level error boundary.
 *
 * A render-time exception anywhere in the tree (e.g. an unexpected non-string
 * value coming back from an LLM-generated payload) would otherwise unmount the
 * whole React app and leave a blank white screen. This boundary catches that,
 * logs it, and shows a recoverable fallback instead.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, message: null };
  }

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surfaced in the browser console for debugging; the UI stays usable.
    console.error('Unhandled render error caught by ErrorBoundary:', error, info);
  }

  private handleReload = () => {
    this.setState({ hasError: false, message: null });
    window.location.reload();
  };

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    // Translate via the i18next singleton (this is a class component, and a
    // render-crash context can't rely on the useTranslation hook). Every
    // lookup carries an English defaultValue so the fallback still works even
    // if i18n failed to initialise.
    const t = (key: string, defaultValue: string) => i18n.t(key, { defaultValue });

    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
        <h1 className="text-lg font-semibold">
          {t('common.errorBoundary.title', 'Something went wrong')}
        </h1>
        <p className="max-w-md text-sm text-foreground-muted">
          {t(
            'common.errorBoundary.body',
            "The app hit an unexpected error and couldn't render this view. Your data is safe — reloading usually fixes it."
          )}
        </p>
        {this.state.message ? (
          <pre className="max-w-md overflow-x-auto rounded-md bg-background-muted px-3 py-2 text-left text-xs text-foreground-muted">
            {this.state.message}
          </pre>
        ) : null}
        <button
          type="button"
          onClick={this.handleReload}
          className="rounded-md border border-border bg-background-elevated px-4 py-2 text-sm font-medium hover:bg-background-muted"
        >
          {t('common.errorBoundary.reload', 'Reload')}
        </button>
      </div>
    );
  }
}
