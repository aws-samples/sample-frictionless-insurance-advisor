import i18n from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';

import enCommon from './locales/en/common.json';
import enAuth from './locales/en/auth.json';
import enAssistant from './locales/en/assistant.json';
import enDomain from './locales/en/domain.json';

import jaCommon from './locales/ja/common.json';
import jaAuth from './locales/ja/auth.json';
import jaAssistant from './locales/ja/assistant.json';
import jaDomain from './locales/ja/domain.json';

import koCommon from './locales/ko/common.json';
import koAuth from './locales/ko/auth.json';
import koAssistant from './locales/ko/assistant.json';
import koDomain from './locales/ko/domain.json';

import esCommon from './locales/es/common.json';
import esAuth from './locales/es/auth.json';
import esAssistant from './locales/es/assistant.json';
import esDomain from './locales/es/domain.json';

import frCommon from './locales/fr/common.json';
import frAuth from './locales/fr/auth.json';
import frAssistant from './locales/fr/assistant.json';
import frDomain from './locales/fr/domain.json';

/**
 * Single source of truth for which locales the UI offers.
 *
 * To add a new locale:
 *   1. Create `src/i18n/locales/<code>/{common,auth,assistant,domain}.json`
 *   2. Add the JSON imports above and the merge entry in `resources` below
 *   3. Append an entry to this array (code + display name)
 */
export const SUPPORTED_LOCALES: readonly { code: string; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'ja', label: '日本語' },
  { code: 'ko', label: '한국어' },
  { code: 'es', label: 'Español' },
  { code: 'fr', label: 'Français' },
];

const DEFAULT_LOCALE = 'en';

/**
 * Merge a locale's per-namespace bundles into a single object suitable for
 * the `translation` namespace. Each namespace JSON is wrapped under an
 * outer key matching its name (e.g. `common.json` starts with
 * `{ "common": {...} }`), so callers use 3+ segment dotted paths like
 * `t('common.actions.send')` or `t('assistant.chat.thinking')`.
 */
function mergeBundles(
  ...bundles: Record<string, unknown>[]
): Record<string, unknown> {
  return Object.assign({}, ...bundles);
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: mergeBundles(enCommon, enAuth, enAssistant, enDomain) },
      ja: { translation: mergeBundles(jaCommon, jaAuth, jaAssistant, jaDomain) },
      ko: { translation: mergeBundles(koCommon, koAuth, koAssistant, koDomain) },
      es: { translation: mergeBundles(esCommon, esAuth, esAssistant, esDomain) },
      fr: { translation: mergeBundles(frCommon, frAuth, frAssistant, frDomain) },
    },
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: SUPPORTED_LOCALES.map((l) => l.code),
    // Strip region variants (en-US → en, ja-JP → ja) before matching.
    load: 'languageOnly',
    nonExplicitSupportedLngs: true,
    ns: ['translation'],
    defaultNS: 'translation',
    // Disable the namespace separator so callers always use a single
    // dotted path (e.g. `t('common.actions.send')`) instead of the legacy
    // `<ns>:` colon syntax.
    nsSeparator: false,
    interpolation: { escapeValue: false },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'i18nextLng',
    },
    returnNull: false,
  });

/**
 * Wire the Amplify Authenticator's vocabulary to the active i18next language.
 * We merge Amplify UI's built-in translations first, then overlay our own
 * `auth.json` so we can add keys Amplify doesn't ship and override wording.
 */
function syncAmplifyLanguage(_lng: string): void {
  // Amplify Authenticator no longer used. Auth UI is custom now (AuthGate).
  // Keeping this hook in place lets us re-introduce vocabulary syncing if
  // we ever bring back the Amplify <Authenticator>.
}

syncAmplifyLanguage(i18n.language);
i18n.on('languageChanged', syncAmplifyLanguage);

export default i18n;
