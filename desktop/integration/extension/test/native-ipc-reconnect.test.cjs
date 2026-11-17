'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const {pathToFileURL} = require('node:url');
const ts = require('../../../vscode/node_modules/typescript');

test('renderer reload replaces the IPC connection and releases its message listeners', {timeout:5000}, async () => {
 const vscode = path.join(__dirname, '../../../vscode');
 // Use the real built IPC/event implementation; mock only Electron's native bus.
 const load = file => import(pathToFileURL(path.join(vscode, 'out', file)).href);
 const [event, lifecycle, buffer, ipc, electron] = await Promise.all([
  load('vs/base/common/event.js'),
  load('vs/base/common/lifecycle.js'),
  load('vs/base/common/buffer.js'),
  load('vs/base/parts/ipc/common/ipc.js'),
  load('vs/base/parts/ipc/common/ipc.electron.js')
 ]);
 const source = fs.readFileSync(path.join(vscode, 'src/vs/base/parts/ipc/electron-main/ipc.electron.ts'), 'utf8');
 const bus = new EventEmitter();
 let receiver;
 const webContents = {
  id:101,
  send(_channel, data) {
   const target = receiver;
   queueMicrotask(() => target.fire(buffer.VSBuffer.wrap(data)));
  }
 };
 const dependencies = {
  './ipcMain.js':{validatedIpcMain:bus},
  '../../../common/buffer.js':buffer,
  '../../../common/event.js':event,
  '../../../common/lifecycle.js':lifecycle,
  '../common/ipc.js':ipc,
  '../common/ipc.electron.js':electron
 };
 const box = {exports:{}, queueMicrotask, require:name => {
  if (!Object.hasOwn(dependencies, name)) throw Error('Unexpected IPC dependency: ' + name);
  return dependencies[name];
 }};
 vm.runInNewContext(ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText, box);
 const server = new box.exports.Server();
 const clients = [], receivers = [], counts = [];
 let removed = 0;
 const removedListener = server.onDidRemoveConnection(() => removed++);
 try {
  for (let reload = 0; reload < 3; reload++) {
   receiver = new event.Emitter();
   receivers.push(receiver);
   bus.emit('vscode:hello', {sender:webContents});
   // A vanished renderer cannot call dispose on its previous IPCClient.
   clients.push(new ipc.IPCClient({
    onMessage:receiver.event,
    send:message => queueMicrotask(() => bus.emit('vscode:message', {sender:webContents}, Buffer.from(message.buffer)))
   }, 'window:101'));
   await new Promise(setImmediate);
   counts.push(server.connections.length);
  }
  bus.emit('vscode:disconnect', {sender:webContents}, null);
  await new Promise(setImmediate);
  assert.deepEqual({
   connectionsAcrossReloads:counts,
   connectionsAfterDisconnect:server.connections.length,
   messageListenersAfterDisconnect:bus.listenerCount('vscode:message'),
   removedConnections:removed
  }, {
   connectionsAcrossReloads:[1,1,1],
   connectionsAfterDisconnect:0,
   messageListenersAfterDisconnect:0,
   removedConnections:3
  });
 } finally {
  removedListener.dispose();
  server.dispose();
  for (const client of clients) client.dispose();
  for (const emitter of receivers) emitter.dispose();
 }
});
