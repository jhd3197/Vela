import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
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
