import {it,expect} from 'vitest';
import {resetCountdown,resetTimestamp,usageSnapshot} from './quotaReset';
const now=Date.parse('2026-11-15T12:00:00Z'),reset=now+3*86400000+4*3600000;
it('uses absolute provider ISO, epoch seconds or epoch milliseconds consistently',()=>{for(const value of [new Date(reset).toISOString(),reset,reset/1000,String(reset/1000)])expect(resetCountdown(value,now)).toEqual({label:'Resets in 3d 4h',at:reset})});
it('shows hours or minutes for short windows',()=>{expect(resetCountdown(now+2*3600000+7*60000,now).label).toBe('Resets in 2h 7m');expect(resetCountdown(now+1000,now).label).toBe('Resets in 1m')});
it('does not invent reset dates when missing, invalid or expired',()=>{for(const value of [null,undefined,'','bad date',0,NaN]){expect(resetTimestamp(value)).toBeUndefined();expect(resetCountdown(value,now).label).toBe('Reset time unavailable')}expect(resetCountdown(now-1,now).label).toBe('Reset due · refresh usage')});

it('keeps stale source observations distinct from refresh checks and future reset dates',()=>{expect(usageSnapshot({cached_at:now-2*86400000,fetched_at:now},now).label).toBe('Cached usage 2d ago');expect(usageSnapshot({rollout_at:now-2*3600000,fetched_at:now},now).label).toBe('Transcript usage snapshot 2h ago');expect(usageSnapshot({fetched_at:now},now).label).toBe('Usage checked just now');expect(usageSnapshot({windows:[{resets_at:reset}]},now).label).toBe('Usage snapshot time unavailable')});
