import { FormEvent, ReactNode, useEffect, useState } from 'react';

import { Mail, Lock, ArrowLeft } from 'lucide-react';
import {
  fetchAuthSession,
  getCurrentUser,
  signIn,
  signOut,
} from 'aws-amplify/auth';
import { useTranslation } from 'react-i18next';

import { signupUser } from '../lib/api';
import { Alert, Button, Card, Logo, Spinner } from '../ui';

interface AuthedUser {
  email: string | null;
}

interface AuthGateProps {
  children: (auth: { advisorEmail: string | null; onSignOut: () => Promise<void> }) => ReactNode;
}

type AuthMode = 'signin' | 'signup';

const MIN_PASSWORD_LEN = 12;

/**
 * Replacement for Amplify's <Authenticator>. Two reasons:
 *
 * 1. The org policy in this account flips AllowAdminCreateUserOnly back to
 *    True on the user pool, which breaks Amplify's self-service SignUp.
 *    We work around it via a public POST /signup Lambda that uses
 *    admin_create_user + admin_set_user_password. Same admin-API pattern.
 * 2. Custom auth UI lets us match the new design system end-to-end (no
 *    Amplify CSS overrides needed).
 *
 * After successful sign-up we sign the user straight in — the Lambda sets
 * a permanent password so the account is CONFIRMED immediately.
 */
export function AuthGate({ children }: AuthGateProps) {
  const [user, setUser] = useState<AuthedUser | null>(null);
  const [bootstrapping, setBootstrapping] = useState(true);

  // On mount: see if there's already a session (refresh-survival).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await getCurrentUser();
        const session = await fetchAuthSession();
        const email =
          (session.tokens?.idToken?.payload?.email as string | undefined) ?? null;
        if (!cancelled) setUser({ email });
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setBootstrapping(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleAuthenticated = (email: string | null) => {
    setUser({ email });
  };

  const handleSignOut = async () => {
    try {
      await signOut();
    } finally {
      setUser(null);
    }
  };

  if (bootstrapping) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-background text-foreground">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!user) {
    return <AuthShell onAuthenticated={handleAuthenticated} />;
  }

  return <>{children({ advisorEmail: user.email, onSignOut: handleSignOut })}</>;
}

/* -------------------------------------------------------------------------- */

interface AuthShellProps {
  onAuthenticated: (email: string | null) => void;
}

function AuthShell({ onAuthenticated }: AuthShellProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<AuthMode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const switchMode = (next: AuthMode) => {
    setMode(next);
    setError(null);
    setPassword('');
    setConfirmPassword('');
  };

  const finishSignIn = async (e: string) => {
    try {
      const session = await fetchAuthSession();
      const tokenEmail =
        (session.tokens?.idToken?.payload?.email as string | undefined) ?? e;
      onAuthenticated(tokenEmail);
    } catch {
      onAuthenticated(e);
    }
  };

  const handleSignIn = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError(t('auth.errors.emailAndPasswordRequired'));
      return;
    }
    setBusy(true);
    try {
      const result = await signIn({
        username: email.trim().toLowerCase(),
        password,
      });
      if (!result.isSignedIn) {
        setError(
          t('auth.errors.signInIncomplete', { step: result.nextStep?.signInStep ?? 'unknown' })
        );
        return;
      }
      await finishSignIn(email.trim().toLowerCase());
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.errors.signInFailed'));
    } finally {
      setBusy(false);
    }
  };

  const handleSignUp = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    const cleanEmail = email.trim().toLowerCase();
    if (!cleanEmail) {
      setError(t('auth.errors.emailRequired'));
      return;
    }
    if (password.length < MIN_PASSWORD_LEN) {
      setError(t('auth.errors.passwordTooShort', { min: MIN_PASSWORD_LEN }));
      return;
    }
    if (password !== confirmPassword) {
      setError(t('auth.errors.passwordsDoNotMatch'));
      return;
    }

    setBusy(true);
    try {
      await signupUser(cleanEmail, password);
      // Account is created + confirmed server-side. Sign in straight away.
      const result = await signIn({ username: cleanEmail, password });
      if (!result.isSignedIn) {
        // Highly unusual — surface and stay on the form so the user can retry.
        setError(
          t('auth.errors.signUpSignInIncomplete', { step: result.nextStep?.signInStep ?? 'unknown' })
        );
        return;
      }
      await finishSignIn(cleanEmail);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.errors.signUpFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background px-4 text-foreground">
      <Card feature className="w-full max-w-md p-6 sm:p-8">
        <div className="flex flex-col items-center gap-3 pb-2">
          <Logo className="h-10 w-10" />
          <div className="text-center">
            <h1 className="text-xl font-bold tracking-tight">
              <span className="text-brand-gradient">{t('auth.brand.title')}</span>
            </h1>
            <p className="mt-1 text-sm text-foreground-muted">{t('auth.brand.tagline')}</p>
          </div>
        </div>

        {/* Mode tabs */}
        <div className="mt-4 grid grid-cols-2 rounded-lg border border-border bg-background-muted p-0.5">
          <button
            type="button"
            onClick={() => switchMode('signin')}
            className={tabClass(mode === 'signin')}
          >
            {t('auth.actions.signIn')}
          </button>
          <button
            type="button"
            onClick={() => switchMode('signup')}
            className={tabClass(mode === 'signup')}
          >
            {t('auth.actions.createAccount')}
          </button>
        </div>

        {error ? (
          <div className="mt-4">
            <Alert variant="danger" onDismiss={() => setError(null)}>
              {error}
            </Alert>
          </div>
        ) : null}

        {mode === 'signin' ? (
          <form className="mt-4 flex flex-col gap-3" onSubmit={handleSignIn}>
            <FieldEmail value={email} onChange={setEmail} placeholder={t('auth.fields.emailPlaceholder')} />
            <FieldPassword
              value={password}
              onChange={setPassword}
              placeholder={t('auth.fields.passwordPlaceholder')}
            />
            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              loading={busy}
              disabled={busy}
            >
              {t('auth.actions.signIn')}
            </Button>
          </form>
        ) : (
          <form className="mt-4 flex flex-col gap-3" onSubmit={handleSignUp}>
            <FieldEmail value={email} onChange={setEmail} placeholder={t('auth.fields.emailPlaceholder')} />
            <FieldPassword
              value={password}
              onChange={setPassword}
              placeholder={t('auth.fields.passwordMinPlaceholder', { min: MIN_PASSWORD_LEN })}
            />
            <FieldPassword
              value={confirmPassword}
              onChange={setConfirmPassword}
              placeholder={t('auth.fields.confirmPasswordPlaceholder')}
            />
            <p className="text-[11px] text-foreground-muted">{t('auth.hint.passwordRules')}</p>
            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              loading={busy}
              disabled={busy}
            >
              {t('auth.actions.createAccount')}
            </Button>
            <button
              type="button"
              className="mx-auto inline-flex items-center gap-1 text-xs text-foreground-muted hover:text-foreground"
              onClick={() => switchMode('signin')}
            >
              <ArrowLeft className="h-3 w-3" />
              {t('auth.actions.backToSignIn')}
            </button>
          </form>
        )}
      </Card>
    </div>
  );
}

function tabClass(active: boolean): string {
  return [
    'rounded-md px-3 py-1.5 text-sm font-medium transition-[background,color,box-shadow]',
    active
      ? 'nav-pill-active'
      : 'text-foreground-muted hover:text-foreground hover:bg-background-elevated',
  ].join(' ');
}

interface FieldProps {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}

function FieldEmail({ value, onChange, placeholder }: FieldProps) {
  return (
    <label className="relative block">
      <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-muted" />
      <input
        type="email"
        autoComplete="email"
        required
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-10 w-full rounded-md border border-border bg-background-elevated pl-9 pr-3 text-sm placeholder:text-foreground-muted/70 focus-visible:border-ring/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
      />
    </label>
  );
}

function FieldPassword({ value, onChange, placeholder }: FieldProps) {
  return (
    <label className="relative block">
      <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-muted" />
      <input
        type="password"
        autoComplete="current-password"
        required
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-10 w-full rounded-md border border-border bg-background-elevated pl-9 pr-3 text-sm placeholder:text-foreground-muted/70 focus-visible:border-ring/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
      />
    </label>
  );
}
