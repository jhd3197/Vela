import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev proxy targets the backend Vela is running on. The dev launcher
// (./dev.sh / .\dev.ps1) sets VELA_BACKEND_URL when it uses a non-default port.
const backendTarget = process.env.VELA_BACKEND_URL || 'http://localhost:7700';

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
        target: backendTarget,
        changeOrigin: true,
      },
      // Installed app content (web apps + reverse-proxied process apps).
      '/apps': {
        target: backendTarget,
        changeOrigin: true,
      },
      // Bundled wallpapers live in the backend's data directory, not in
      // web/public: the first start downloads and derives them there.
      '/wallpapers': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
});
