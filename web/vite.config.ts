import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The API has no CORS and is bound to loopback on purpose (spec section 15.3), so the dev
// server proxies rather than the server relaxing. `/api` covers the SSE stream at
// /api/v1/events too, which http-proxy streams without buffering.
//
// Both ends are overridable so a second stack can run beside the default one — checking a
// change against a freshly built engine while another API is still serving the old one is
// otherwise a matter of stopping the first, which loses whatever it was doing.
const PORT = Number(process.env.CRACKTRADE_WEB_PORT ?? 5173)
const API = process.env.CRACKTRADE_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: PORT,
    // Fail rather than drift to 5174 when the port is taken: .claude/launch.json declares
    // 5173, and a server that quietly moved would leave the preview pointing at nothing.
    strictPort: true,
    proxy: {
      '/api': {
        target: API,
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
