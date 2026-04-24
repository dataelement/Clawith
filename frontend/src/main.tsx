import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import './i18n';
import './index.css';
import App from './App';
import ErrorBoundary from './components/ErrorBoundary';
import { DialogProvider } from './components/Dialog/DialogProvider';
import { ToastProvider } from './components/Toast/ToastProvider';
import { loadSavedAccentColor } from './utils/theme';

// Apply saved theme color before first paint
loadSavedAccentColor();

const queryClient = new QueryClient({
    defaultOptions: {
        queries: { retry: 1, refetchOnWindowFocus: false },
    },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
        <ErrorBoundary>
            <QueryClientProvider client={queryClient}>
                <BrowserRouter>
                    <DialogProvider>
                        <ToastProvider>
                            <App />
                        </ToastProvider>
                    </DialogProvider>
                </BrowserRouter>
            </QueryClientProvider>
        </ErrorBoundary>
    </React.StrictMode>,
);
