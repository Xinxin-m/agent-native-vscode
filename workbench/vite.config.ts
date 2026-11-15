import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {cpSync,mkdirSync} from 'node:fs';
export default defineConfig({plugins:[react(),{name:'local-pdf-assets',closeBundle(){for(const dir of ['cmaps','standard_fonts','wasm']){mkdirSync('../web/workbench/pdfjs',{recursive:true});cpSync('node_modules/pdfjs-dist/'+dir,'../web/workbench/pdfjs/'+dir,{recursive:true});}}}],base:'/static/workbench/',resolve:{dedupe:['dompurify']},build:{outDir:'../web/workbench',emptyOutDir:false},server:{proxy:{'/api':'http://127.0.0.1:8744'}}});
