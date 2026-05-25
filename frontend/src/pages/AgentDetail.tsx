import React, { Component, ErrorInfo } from 'react';
import { useTranslation } from 'react-i18next';

import AgentDetailPage from './agent-detail/AgentDetailPage';

function ErrorFallback({ error, onReload }: { error: Error | null; onReload: () => void }) {
    const { t } = useTranslation();
    return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', gap: '16px' }}>
            <div style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>{t('agentDetail.errorTitle')}</div>
            <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', maxWidth: '400px', textAlign: 'center' }}>
                {error?.message || t('agentDetail.errorMessage')}
            </div>
            <button
                className="btn btn-primary"
                onClick={onReload}
                style={{ marginTop: '8px' }}
            >
                {t('agentDetail.reloadPage')}
            </button>
        </div>
    );
}

class AgentDetailErrorBoundary extends Component<{ children: React.ReactNode }, { hasError: boolean; error: Error | null }> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error) {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('AgentDetail crash caught by error boundary:', error, errorInfo);
    }

    handleReload = () => {
        this.setState({ hasError: false, error: null });
        window.location.reload();
    };

    render() {
        if (this.state.hasError) {
            return <ErrorFallback error={this.state.error} onReload={this.handleReload} />;
        }

        return this.props.children;
    }
}

export default function AgentDetailWithErrorBoundary() {
    return (
        <AgentDetailErrorBoundary>
            <AgentDetailPage />
        </AgentDetailErrorBoundary>
    );
}
