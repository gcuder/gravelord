var _a;
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
var BACKEND = (_a = process.env.GRAVELORD_BACKEND) !== null && _a !== void 0 ? _a : "http://127.0.0.1:7777";
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: BACKEND,
                changeOrigin: true,
                ws: true,
            },
        },
    },
    build: {
        outDir: "dist",
        emptyOutDir: true,
    },
});
