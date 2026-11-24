import {existsSync,readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {expect,test} from 'vitest';
import {PINCH_JS} from './pinch';
const backend=resolve(__dirname,'../../../../../chat-dashboard/preview_server.py');
test.skipIf(!existsSync(backend))('fork decks use the same pinch handler as the backend preview',()=>{
 expect(PINCH_JS).toBe(/PINCH_JS = """([\s\S]*?)"""/.exec(readFileSync(backend,'utf8'))![1]);
});
