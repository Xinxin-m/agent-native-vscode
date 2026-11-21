import {describe,it,expect} from 'vitest';
import {textSizes,nearestTextSize,textScale} from './textSize';
describe('interface text sizes',()=>{it('offers five named sizes with medium at the baseline',()=>{expect(textSizes.map(x=>x.label)).toEqual(['Smallest','Small','Medium','Large','Largest']);expect(textSizes.map(x=>x.value)).toEqual([12,13,14,16,18]);expect(textScale(14)).toBe(1);});it('maps old saved values to the nearest supported choice',()=>{expect(nearestTextSize(15)).toBe(14);expect(nearestTextSize(22)).toBe(18);expect(nearestTextSize(NaN)).toBe(14);});});
