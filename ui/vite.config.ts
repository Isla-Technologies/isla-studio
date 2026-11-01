import preact from '@preact/preset-vite';
import { defineConfig } from 'vite';

// The Python server (python -m isla_rl.server) serves the built app and its API on
// one port. In development, Vite serves the app and forwards the API to it.
const server = process.env.ISLA_SERVER ?? 'http://127.0.0.1:8848';

export default defineConfig({
  plugins: [preact()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': server,
      '/ws': { target: server.replace('http', 'ws'), ws: true },
    },
  },
});
