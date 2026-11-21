const test=require('node:test'),assert=require('node:assert/strict');const {aliasFor}=require('../remote-browser.cjs');
test('folder browsing uses only the configured data-transfer alias',()=>{const host={kind:'slurm',read_alias:'cluster-dtn',execution_alias:'cluster-compute',login_aliases:['cluster']};
 assert.equal(aliasFor(host),'cluster-dtn');
 assert.throws(()=>aliasFor({...host,read_alias:'cluster'}),/data-transfer/);
 assert.throws(()=>aliasFor({...host,read_alias:'cluster-compute'}),/data-transfer/);
 assert.throws(()=>aliasFor({...host,read_alias:'login.cluster.example'}),/data-transfer/);
 assert.equal(aliasFor({kind:'ssh',read_alias:'other-mac'}),'other-mac');});
