import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],base:'./',build:{outDir:'../extension/media',emptyOutDir:false,rollupOptions:{input:{boot:'src/native/boot.ts',document:'src/native/document.ts',pdf:'src/native/pdf.ts'},output:{entryFileNames:'[name].js',chunkFileNames:'[name]-[hash].js',assetFileNames:'[name][extname]'}}}});
