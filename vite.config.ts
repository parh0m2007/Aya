import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export default defineConfig({
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**", "**/node_modules/**"],
    },
  },
  build: {
    target: "es2021",
    outDir: "dist",
    rollupOptions: {
      input: {
        main: resolve(fileURLToPath(new URL("./index.html", import.meta.url))),
        console: resolve(fileURLToPath(new URL("./console.html", import.meta.url))),
      },
    },
  },
});
