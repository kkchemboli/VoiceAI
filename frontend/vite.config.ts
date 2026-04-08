import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const port = Number(process.env.PORT || 5173)

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port,
    strictPort: true,
    // Required when running behind reverse proxies (like Coolify wildcard domains)
    // so requests from your public domain are accepted.
    allowedHosts: true,
  },
})
