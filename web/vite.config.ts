import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server: proxy /api to the content engine and add the service token here, so it never reaches the browser.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', '');
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.ENGINE_URL ?? 'http://localhost:8002',
          headers: { Authorization: `Bearer ${env.ENGINE_TOKEN_WEB ?? ''}` },
        },
      },
    },
  };
});
