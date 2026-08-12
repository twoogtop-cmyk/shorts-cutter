import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// В dev-режиме фронт работает локально, а /api и /media проксируются
// на сервер — отдельный локальный backend поднимать не нужно.
const SERVER = process.env.SC_SERVER_URL || 'http://83.166.244.157'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: SERVER, changeOrigin: true },
      '/media': { target: SERVER, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 900,
  },
})
