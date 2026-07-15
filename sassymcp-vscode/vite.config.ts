import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// Builds the React cockpit (webview/) into media/cockpit/ as a single
// self-contained IIFE bundle (cockpit.js) + one stylesheet (cockpit.css),
// referenced by src/cockpit.ts with a CSP nonce. IIFE (not ESM) keeps the
// webview <script> a classic tag — simplest under VS Code's CSP.
export default defineConfig({
    plugins: [react()],
    // Library builds don't replace process.env.NODE_ENV — without this, React's
    // bundle hits a bare `process` at runtime in a webview ("process is not
    // defined") and never mounts. Pin it so the IIFE is self-contained.
    define: {
        "process.env.NODE_ENV": JSON.stringify("production"),
        "process.env": "{}",
    },
    build: {
        outDir: resolve(__dirname, "media/cockpit"),
        emptyOutDir: true,
        cssCodeSplit: false,
        lib: {
            entry: resolve(__dirname, "webview/main.tsx"),
            formats: ["iife"],
            name: "SassyCockpit",
            fileName: () => "cockpit.js",
        },
        rollupOptions: {
            output: {
                assetFileNames: (info) =>
                    info.name && info.name.endsWith(".css") ? "cockpit.css" : "assets/[name][extname]",
            },
        },
    },
});
