import {describe,expect,it} from 'vitest';
import {moveMenuItem} from './OrderedMenu';

describe('menu ordering',()=>{
 it('moves an action before the first row and preserves the other positions',()=>{
  const defaultOrder=['groups','maximize','close-pane','arrange'];
  expect(moveMenuItem(defaultOrder,'close-pane','groups',false)).toEqual(['close-pane','groups','maximize','arrange']);
  expect(defaultOrder).toEqual(['groups','maximize','close-pane','arrange']);
 });
 it('moves after a row and keeps stored positions for temporarily unavailable actions',()=>{
  const order=['groups','pending-window','close-pane','arrange'];
  expect(moveMenuItem(order,'groups','close-pane',true)).toEqual(['pending-window','close-pane','groups','arrange']);
  expect(moveMenuItem(order,'groups','unknown',true)).toBe(order);
 });
});
