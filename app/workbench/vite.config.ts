import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {cpSync,mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
let outputDir='';
export default defineConfig({plugins:[react(),{name:'local-pdf-assets',configResolved(config){outputDir=resolve(config.root,config.build.outDir);},closeBundle(){const output=resolve(outputDir,'pdfjs');for(const dir of ['cmaps','standard_fonts','wasm']){mkdirSync(output,{recursive:true});cpSync('node_modules/pdfjs-dist/'+dir,resolve(output,dir),{recursive:true});}}}],base:'/static/workbench/',resolve:{dedupe:['dompurify']},build:{outDir:'../web/workbench',emptyOutDir:false},server:{proxy:{'/api':'http://127.0.0.1:8744'}}});
