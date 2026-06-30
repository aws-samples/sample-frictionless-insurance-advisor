import 'i18next';

import common from './locales/en/common.json';
import auth from './locales/en/auth.json';
import assistant from './locales/en/assistant.json';
import domain from './locales/en/domain.json';

// Augment i18next's type surface so dotted-path keys like
// `t('common.actions.send')`, `t('assistant.chat.send')`, etc. compile.
//
// Each locale JSON is wrapped under an outer key matching its namespace
// (e.g. common.json starts with `{ "common": { ... } }`). The default
// namespace is `translation`, whose shape is the intersection of every
// per-namespace bundle — t() therefore accepts any dotted-path key from
// any locale namespace at compile time, and the runtime merges all
// namespaces into the `translation` resource.
declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation';
    resources: {
      translation: typeof common & typeof auth & typeof assistant & typeof domain;
    };
  }
}
