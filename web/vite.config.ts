import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The node (scripts/run_api.py) serves the HTTP API on 127.0.0.1:8000.
// In dev we proxy /api/* there so the browser only ever talks to the Vite
// origin and CORS never enters the picture. Override the upstream with
// API_PROXY_TARGET if the node runs elsewhere.
// Declared locally rather than pulling in @types/node just for this line.
declare const process: { env: Record<string, string | undefined> }

const target = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target,
        changeOrigin: true,
      },
    },
  },
})
