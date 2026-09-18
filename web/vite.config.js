import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two pages, not one. The dashboard is what a person opens; `agent-host`
      // is what an agent desktop's browser loads, and it deliberately shares
      // none of the dashboard's chrome.
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        'agent-host': resolve(import.meta.dirname, 'agent-host.html'),
      },
    },
  },
  css: {
    preprocessorOptions: { scss: { api: 'modern' } },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:7700',
        changeOrigin: true,
      },
      // Installed app content (web apps + reverse-proxied process apps).
      '/apps': {
        target: 'http://localhost:7700',
        changeOrigin: true,
      },
    },
  },
});
