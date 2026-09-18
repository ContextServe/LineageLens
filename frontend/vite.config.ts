import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // The dashboard is a pure client of the local HTTP surface, so the dev
    // server proxies rather than hardcoding an origin. Matches the default
    // bind of `lineagelens serve`, which is loopback-only on purpose.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
