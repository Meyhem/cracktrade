import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The API has no CORS and is bound to loopback on purpose (spec section 15.3), so the dev
// server proxies rather than the server relaxing. `/api` covers the SSE stream at
// /api/v1/events too, which http-proxy streams without buffering.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    css: false,
  },
})
