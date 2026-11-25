import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // Split heavy vendor libraries into their own chunks so they
        // can cache independently across deploys. NOTE: only put libs
        // here that are referenced from the *main* bundle's static
        // imports — anything used only inside lazy-loaded page chunks
        // (e.g. react-markdown, only used in AssistantPage) should NOT
        // be listed here, otherwise it ends up preloaded eagerly via
        // <link rel="modulepreload"> even though the page-chunk
        // dynamic import is what should trigger it.
        //
        // Vite 8 uses Rolldown, which groups chunks with
        // `codeSplitting.groups` and regex `test` matchers on module ids.
        codeSplitting: {
          groups: [
            // Auth + AWS SDK pieces — referenced from AuthGate (eager
            // first paint) and from every API call (api.ts + agentcore.ts).
            // Has to be first-paint, but caching it separately means the
            // 250+ KB chunk doesn't bust on every app code change.
            {
              name: 'aws-amplify',
              test: /[\\/]node_modules[\\/](aws-amplify|@aws-amplify|@aws-sdk|@smithy)[\\/]/,
            },
            // i18n bundle — strings + react bindings. Loaded eagerly but
            // kept separate so it can be cached independently across deploys.
            {
              name: 'i18n',
              test: /[\\/]node_modules[\\/](i18next|i18next-browser-languagedetector|react-i18next)[\\/]/,
            },
          ],
        },
      },
    },
    // The audio worklet is loaded with audioWorklet.addModule(url). Vite would inline a file this small as a
    // data: URL, which the Content-Security-Policy (script-src 'self') does not allow, so it is always emitted
    // as a hashed file under assets/. Every other asset keeps the default inline limit.
    assetsInlineLimit: (filePath) => (filePath.endsWith('.worklet.js') ? false : undefined),
    // Keep Vite's default warning threshold but raise it slightly so the
    // first vendor chunk (aws-amplify, ~400 KB pre-gzip) doesn't trigger
    // false alarms after we've explicitly chunked it.
    chunkSizeWarningLimit: 600,
  },
});
