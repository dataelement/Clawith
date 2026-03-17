import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

// Read version from local VERSION file first, fallback to root VERSION
let majorVersion = '0.0.0'
for (const candidate of ['./VERSION', '../VERSION']) {
  try {
    majorVersion = fs.readFileSync(path.resolve(__dirname, candidate), 'utf-8').trim()
    break
  } catch {
    // try next candidate
  }
}
const now = new Date()
const buildTimestamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}.${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`
const version = `${majorVersion}+${buildTimestamp}`

export default defineConfig({
    plugins: [react()],
    define: {
        __APP_VERSION__: JSON.stringify(version),
    },
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
        },
    },
    server: {
        port: 3008,
        host: '0.0.0.0',
        proxy: {
            '/api': {
                target: 'http://localhost:8008',
                changeOrigin: true,
            },
            '/ws': {
                target: 'ws://localhost:8008',
                ws: true,
            },
        },
    },
})
