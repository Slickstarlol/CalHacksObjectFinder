import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// FastAPI (server.py) serves images (/snapshots/*) and Grok chat (/api/*).
const FASTAPI = 'http://localhost:8000';

// https://vite.dev/config/
export default defineConfig(({ command, isPreview }) => ({
  plugins: [react()],
  // Production build is mounted by FastAPI at /app; dev server stays at /.
  base: command === 'build' || isPreview ? '/app/' : '/',
  // public/ only holds demo fixture images (for demo.html); keep them out of the prod bundle.
  publicDir: command === 'build' ? false : 'public',
  server: {
    // :5173 belongs to frontend/dashboard/ (the JSX dashboard); this one uses :5174.
    port: 5174,
    host: true, // reachable from a phone on the same Wi-Fi: http://<laptop-ip>:5174
    proxy: {
      '/api': { target: FASTAPI, changeOrigin: true },
      '/snapshots': { target: FASTAPI, changeOrigin: true },
    },
  },
  preview: {
    proxy: {
      '/api': { target: FASTAPI, changeOrigin: true },
      '/snapshots': { target: FASTAPI, changeOrigin: true },
    },
  },
}));
