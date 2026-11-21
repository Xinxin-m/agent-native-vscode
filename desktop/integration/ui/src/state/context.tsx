import {createContext,useContext} from 'react';
import type {WorkbenchContextValue} from './types';
export const WorkbenchContext=createContext<WorkbenchContextValue>(null);
export function useWorkbench(){return useContext(WorkbenchContext);}
