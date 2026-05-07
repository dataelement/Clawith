import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconDownload, IconX } from '@tabler/icons-react';
import MarkdownRenderer from './MarkdownRenderer';
import { classifyFile } from '../utils/fileTypes';
import { useAuthStore } from '../stores';

export interface FilePreviewModalProps {
    filename: string;
    mimeType?: string;
    /** Authenticated download URL like `/api/projects/{pid}/files/{fid}`.
     *  We always go through fetch() to attach the Bearer token, then use a
     *  blob URL for `<img>` / `<iframe>` so we never embed the auth token
     *  in a query string. */
    downloadUrl: string;
    onClose: () => void;
}

export function FilePreviewModal({ filename, mimeType, downloadUrl, onClose }: FilePreviewModalProps) {
    const { t } = useTranslation();
    const kind = classifyFile(filename, mimeType);
    const [text, setText] = useState<string | null>(null);
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const token = useAuthStore(s => s.token);

    useEffect(() => {
        let cancelled = false;
        const ac = new AbortController();
        let createdBlobUrl: string | null = null;

        const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};

        if (kind === 'markdown' || kind === 'text') {
            fetch(downloadUrl, { headers, signal: ac.signal })
                .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
                .then(txt => { if (!cancelled) setText(txt); })
                .catch(e => { if (!cancelled && e.name !== 'AbortError') setError(String(e?.message || e)); });
        } else if (kind === 'image' || kind === 'pdf') {
            fetch(downloadUrl, { headers, signal: ac.signal })
                .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
                .then(b => {
                    if (cancelled) return;
                    createdBlobUrl = URL.createObjectURL(b);
                    setBlobUrl(createdBlobUrl);
                })
                .catch(e => { if (!cancelled && e.name !== 'AbortError') setError(String(e?.message || e)); });
        }

        return () => {
            cancelled = true;
            ac.abort();
            if (createdBlobUrl) URL.revokeObjectURL(createdBlobUrl);
        };
    }, [downloadUrl, kind, token]);

    useEffect(() => {
        const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onEsc);
        return () => window.removeEventListener('keydown', onEsc);
    }, [onClose]);

    const directDownload = async () => {
        try {
            const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
            const r = await fetch(downloadUrl, { headers });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const b = await r.blob();
            const u = URL.createObjectURL(b);
            const a = document.createElement('a');
            a.href = u;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.setTimeout(() => URL.revokeObjectURL(u), 0);
        } catch (e) {
            console.error('Download failed:', e);
        }
    };

    return (
        <div
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 11000,
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                className="card"
                style={{
                    width: '80vw', maxWidth: 900, height: '80vh',
                    padding: 0, display: 'flex', flexDirection: 'column',
                    overflow: 'hidden',
                }}
            >
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '12px 16px', borderBottom: '1px solid var(--border-primary)',
                }}>
                    <h3 style={{ margin: 0, fontSize: 14, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {filename}
                    </h3>
                    <button className="btn" onClick={directDownload} title={t('common.download', 'Download')}>
                        <IconDownload size={14} stroke={1.75} />
                    </button>
                    <button className="btn" onClick={onClose} title={t('common.close', 'Close')}>
                        <IconX size={14} stroke={1.75} />
                    </button>
                </div>
                <div
                    style={{
                        flex: 1, overflow: 'auto',
                        padding: kind === 'image' || kind === 'pdf' ? 0 : '20px 28px',
                        background: 'var(--bg-primary)',
                    }}
                >
                    {error ? (
                        <div style={{ padding: 24, color: 'var(--error)', textAlign: 'center' }}>
                            {t('projects.files.previewFailed', 'Preview failed')}: {error}
                        </div>
                    ) : kind === 'image' ? (
                        blobUrl ? (
                            <img
                                src={blobUrl}
                                alt={filename}
                                style={{ maxWidth: '100%', maxHeight: '100%', display: 'block', margin: '0 auto' }}
                            />
                        ) : (
                            <div style={{ padding: 24, color: 'var(--text-tertiary)', textAlign: 'center' }}>
                                {t('common.loading', 'Loading...')}
                            </div>
                        )
                    ) : kind === 'pdf' ? (
                        blobUrl ? (
                            <iframe
                                src={blobUrl}
                                title={filename}
                                style={{ width: '100%', height: '100%', border: 'none' }}
                            />
                        ) : (
                            <div style={{ padding: 24, color: 'var(--text-tertiary)', textAlign: 'center' }}>
                                {t('common.loading', 'Loading...')}
                            </div>
                        )
                    ) : kind === 'markdown' ? (
                        text != null ? (
                            <MarkdownRenderer content={text} />
                        ) : (
                            <div style={{ color: 'var(--text-tertiary)' }}>{t('common.loading', 'Loading...')}</div>
                        )
                    ) : kind === 'text' ? (
                        text != null ? (
                            <pre
                                style={{
                                    margin: 0,
                                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                                    fontSize: 13, lineHeight: 1.7,
                                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                }}
                            >{text}</pre>
                        ) : (
                            <div style={{ color: 'var(--text-tertiary)' }}>{t('common.loading', 'Loading...')}</div>
                        )
                    ) : (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                            <div style={{ fontSize: 14, marginBottom: 12 }}>
                                {t('projects.files.cannotPreview', 'Cannot preview this file type')}
                            </div>
                            <button className="btn btn-primary" onClick={directDownload}>
                                <IconDownload size={14} stroke={1.75} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                                {t('common.download', 'Download')}
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export default FilePreviewModal;
