import React, { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { AdminCentre } from './AdminCentre';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AdminCentre />
  </StrictMode>,
);
