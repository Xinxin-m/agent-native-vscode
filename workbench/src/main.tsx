import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import {installTooltips} from './ui/tooltips';
import {installPopupDismissal} from './ui/dismiss';
installPopupDismissal(document);
installTooltips(document);
createRoot(document.getElementById('root')!).render(<App/>);
