import cesium from 'vite-plugin-cesium';
import { defineConfig } from 'vite';

export default defineConfig({
  // Chemins relatifs : le build est servi depuis n'importe quel dossier
  // (dist/ embarqué dans le paquet Jarvis).
  base: './',
  plugins: [cesium()],
  build: {
    outDir: 'dist',
    target: 'es2022',
    sourcemap: false,
    chunkSizeWarningLimit: 12_000,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
  },
});
