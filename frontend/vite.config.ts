import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend so the browser sees a single
// origin. That means no CORS preflight in development and no API base URL baked
// into the bundle — the same build works behind any host in production.
export default defineConfig({
  plugins: [react()],
  resolve: {
    // TypeScript already knows about "@/*" (see tsconfig.json "paths"). Vite and
    // Rollup do NOT read tsconfig, so the same alias must be declared here or the
    // production build fails with "Rollup failed to resolve import "@/App"".
    // Keeping both in sync is the only reason this line exists.
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
        // Server-Sent Events (ingestion progress) must not be buffered.
        ws: false,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          markdown: ["react-markdown", "remark-gfm"],
        },
      },
    },
  },
});
