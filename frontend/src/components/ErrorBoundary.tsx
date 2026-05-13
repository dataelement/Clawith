import React, { Component, ErrorInfo, ReactNode } from 'react';
import { IconAlertTriangle } from '@tabler/icons-react';

interface Props {
    children?: ReactNode;
    fallback?: ReactNode;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

class ErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false,
        error: null
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('Uncaught error:', error, errorInfo);
    }

    public render() {
        if (this.state.hasError) {
            if (this.props.fallback) {
                return this.props.fallback;
            }
            return (
                <div style={{ padding: '20px', color: 'var(--text-primary)', maxWidth: '600px', margin: '40px auto', background: 'var(--bg-card)', borderRadius: '8px', boxShadow: 'var(--shadow-md)' }}>
                    <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '0 0 16px 0', color: 'var(--error)' }}>
                        <IconAlertTriangle size={24} stroke={1.8} /> Oops, something went wrong.
                    </h2>
                    <p style={{ color: 'var(--text-secondary)', marginBottom: '16px' }}>
                        An unexpected error occurred. You can try refreshing the page or contact support if the problem persists.
                    </p>
                    <details style={{ whiteSpace: 'pre-wrap', marginBottom: '20px', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '13px', border: '1px solid var(--border-color)', color: 'var(--error)' }}>
                        <summary style={{ cursor: 'pointer', fontWeight: 'bold', marginBottom: '8px' }}>Error Details</summary>
                        {this.state.error && this.state.error.toString()}
                    </details>
                    <button
                        className="btn btn-primary"
                        onClick={() => window.location.reload()}
                    >
                        Refresh Page
                    </button>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
