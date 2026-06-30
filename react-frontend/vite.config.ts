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
        manualChunks: {
          // Auth + AWS SDK pieces — referenced from AuthGate (eager
          // first paint) and from every API call (api.ts + agentcore.ts).
          // Has to be first-paint, but caching it separately means the
          // 250+ KB chunk doesn't bust on every app code change.
          'aws-amplify': ['aws-amplify'],
          // i18n bundle — strings + react bindings. Loaded eagerly but
          // kept separate so it can be cached independently across deploys.
          i18n: ['i18next', 'i18next-browser-languagedetector', 'react-i18next'],
        },
      },
    },
    // Keep Vite's default warning threshold but raise it slightly so the
    // first vendor chunk (aws-amplify, ~400 KB pre-gzip) doesn't trigger
    // false alarms after we've explicitly chunked it.
    chunkSizeWarningLimit: 600,
  },
});
