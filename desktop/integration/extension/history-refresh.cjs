"use strict";

function createHistoryRefresh({api, redraw, progress = () => {}, warn = () => {}, complete = () => {},
                               delay = () => new Promise(resolve => setTimeout(resolve, 1000))}) {
 let active;
 async function run() {
  let job;
  try {
   job = (await api('/api/sync', {include_remote:true})).refresh;
  } catch (error) {
   redraw('unavailable');
   throw error;
  }
  if (!job?.id) {
   redraw('unavailable');
   return;
  }
  const status = progress('Refreshing chat history…');
  let localDrawn = false;
  try {
   while (job.running) {
    if (job.local_ready && !localDrawn) {
     redraw('local');
     localDrawn = true;
    }
    await delay();
    const result = await api('/api/history_refresh?' + new URLSearchParams({id:job.id}));
    if (result.refresh?.id !== job.id) throw Error('Could not verify the chat history refresh.');
    job = result.refresh;
   }
   if (!localDrawn || job.remote_indexed || job.rescan) redraw('done');
   if (job.errors?.length) warn(job.errors.join(' · '));
   else complete();
  } finally {
   status?.dispose();
  }
 }
 return () => {
  if (!active) active = run().finally(() => {active = undefined;});
  return active;
 };
}

module.exports = {createHistoryRefresh};
