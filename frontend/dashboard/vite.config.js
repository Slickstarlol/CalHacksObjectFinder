import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Proxy the Python server so the dashboard can use the camera, sightings,
// snapshots, and people API from the same origin during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/snapshots': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
