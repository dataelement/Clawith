/**
 * Unified FileBrowser component
 * Replaces duplicated file browsing/editing logic across:
 * - Agent Workspace, Skills, Soul, Memory tabs
 * - Enterprise Knowledge Base
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import MarkdownRenderer from './MarkdownRenderer';

// ─── Types ─────────────────────────────────────────────

export interface FileItem {
    name: string;
    path: string;
    is_dir: boolean;
    size?: number;
}

export interface FileBrowserApi {
    list: (path: string) => Promise<FileItem[]>;
    read: (path: string) => Promise<{ content: string }>;
    write: (path: string, content: string) => Promise<any>;
    delete: (path: string) => Promise<any>;
    upload?: (file: File, path: string, onProgress?: (pct: number) => void) => Promise<any>;
    downloadUrl?: (path: string) => string;
}

export interface FileBrowserProps {
    api: FileBrowserApi;
    rootPath?: string;
    features?: {
        upload?: boolean;
        newFile?: boolean;
        newFolder?: boolean;
        edit?: boolean;
        delete?: boolean;
        directoryNavigation?: boolean;
    };
    fileFilter?: string[];
    singleFile?: string;
    uploadAccept?: string;
    title?: string;
    readOnly?: boolean;
    onRefresh?: () => void;
}

// ─── Text file detection ───────────────────────────────

const TEXT_EXTS = ['.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml', '.js', '.ts', '.py', '.html', '.css', '.sh', '.log', '.gitkeep', '.env'];

function isTextFile(name: string): boolean {
    const n = name.toLowerCase();
    if (TEXT_EXTS.some(ext => n.endsWith(ext))) return true;
    const base = n.split('/').pop() || '';
    return !base.includes('.') || base.startsWith('.');
}

// ─── Component ─────────────────────────────────────────

export default function FileBrowser({
    api,
    rootPath = '',
    features = {},
    fileFilter,
    singleFile,
    uploadAccept = '.pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.json,.xml,.yaml,.yml,.js,.ts,.py,.html,.css,.sh,.log',
    title,
    readOnly = false,
    onRefresh,
}: FileBrowserProps) {
    const { t } = useTranslation();
    const {
        upload = false,
        newFile = false,
        newFolder = false,
        edit = !readOnly,
        delete: canDelete = !readOnly,
        directoryNavigation = false,
    } = features;

    // ─── State ─────────────────────────────────────────
    const [currentPath, setCurrentPath] = useState(rootPath);
    const [files, setFiles] = useState<FileItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [contentLoaded, setContentLoaded] = useState(false);
    const [viewing, setViewing] = useState<string | null>(singleFile || null);
    const [content, setContent] = useState('');
    const [editing, setEditing] = useState(false);
    const [editContent, setEditContent] = useState('');
    const [saving, setSaving] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<{ path: string; name: string } | null>(null);
    const [promptModal, setPromptModal] = useState<{ title: string; placeholder: string; action: string } | null>(null);
    const [promptValue, setPromptValue] = useState('');
    const [uploadProgress, setUploadProgress] = useState<{ fileName: string; percent: number } | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Auto-resize textarea to match content height
    useEffect(() => {
        const el = textareaRef.current;
        if (el && editing) {
            el.style.height = 'auto';
            el.style.height = Math.max(200, el.scrollHeight) + 'px';
        }
    }, [editing, editContent]);

    // ─── Helpers ───────────────────────────────────────

    const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 3000);
    }, []);

    // ─── Load files ───────────────────────────────────

    const reload = useCallback(async () => {
        if (singleFile) {
            // Single-file mode: just load the content
            try {
                const data = await api.read(singleFile);
                setContent(data.content || '');
            } catch {
                setContent('');
            }
            setContentLoaded(true);
            return;
        }
        setLoading(true);
        try {
            let data = await api.list(currentPath);
            if (fileFilter && fileFilter.length > 0) {
                data = data.filter(f => f.is_dir || fileFilter.some(ext => f.name.toLowerCase().endsWith(ext)));
            }
            setFiles(data);
        } catch {
            setFiles([]);
        }
        setLoading(false);
    }, [api, currentPath, singleFile, fileFilter]);

    useEffect(() => { reload(); }, [reload]);

    // ─── Load file content when viewing ───────────────

    useEffect(() => {
        if (!viewing || singleFile) return;
        api.read(viewing).then(data => {
            setContent(data.content || '');
        }).catch(() => setContent(''));
    }, [viewing, api, singleFile]);

    // ─── Actions ──────────────────────────────────────

    const handleSave = async () => {
        const target = singleFile || viewing;
        if (!target) return;
        setSaving(true);
        try {
            await api.write(target, editContent);
            setContent(editContent);
            setEditing(false);
            showToast('Saved');
            onRefresh?.();
        } catch (err: any) {
            showToast('Save failed: ' + (err.message || ''), 'error');
        }
        setSaving(false);
    };

    const handleDelete = async () => {
        if (!deleteTarget) return;
        try {
            await api.delete(deleteTarget.path);
            setDeleteTarget(null);
            if (viewing === deleteTarget.path) {
                setViewing(null);
                setEditing(false);
            }
            reload();
            onRefresh?.();
            showToast('Deleted');
        } catch (err: any) {
            showToast('Delete failed: ' + (err.message || ''), 'error');
        }
    };

    const handleUpload = () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = uploadAccept;
        input.multiple = true;
        input.onchange = async () => {
            if (!input.files || input.files.length === 0) return;
            try {
                const fileList = Array.from(input.files);
                for (const file of fileList) {
                    setUploadProgress({ fileName: file.name, percent: 0 });
                    await api.upload!(file, currentPath, (pct) => {
                        setUploadProgress({ fileName: file.name, percent: pct });
                    });
                }
                setUploadProgress(null);
                reload();
                onRefresh?.();
                showToast('Upload successful');
            } catch (err: any) {
                setUploadProgress(null);
                showToast('Upload failed: ' + (err.message || ''), 'error');
            }
        };
        input.click();
    };

    const handlePromptConfirm = async () => {
        const value = promptValue.trim();
        if (!value || !promptModal) return;
        const action = promptModal.action;
        setPromptModal(null);
        setPromptValue('');
        try {
            if (action === 'newFolder') {
                const folderPath = currentPath ? `${currentPath}/${value}` : value;
                await api.write(`${folderPath}/.gitkeep`, '');
            } else if (action === 'newFile') {
                const filePath = currentPath ? `${currentPath}/${value}` : value;
                await api.write(filePath, '');
                setViewing(filePath);
                setEditContent('');
                setEditing(true);
            } else if (action === 'newSkill') {
                const template = `# ${value}\n\n## Description\n_Describe the purpose and triggers_\n\n## Input\n- Param1: Description\n\n## Steps\n1. Step one\n2. Step two\n\n## Output\n_Describe the output format_\n`;
                const filePath = currentPath ? `${currentPath}/${value}.md` : `${value}.md`;
                await api.write(filePath, template);
                setViewing(filePath);
                setEditContent(template);
                setEditing(true);
            }
            reload();
            onRefresh?.();
        } catch (err: any) {
            showToast('Failed: ' + (err.message || ''), 'error');
        }
    };

    // ─── Breadcrumbs ──────────────────────────────────

    const pathParts = currentPath ? currentPath.split('/').filter(Boolean) : [];

    const renderBreadcrumbs = () => {
        if (!directoryNavigation || singleFile) return null;
        return (
            <div style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '8px', flexWrap: 'wrap' }}>
                <span
                    style={{ cursor: 'pointer', color: 'var(--accent-primary)', fontWeight: 500 }}
                    onClick={() => { setCurrentPath(rootPath); setViewing(null); setEditing(false); }}
                >
                    📁 {rootPath || 'root'}
                </span>
                {pathParts.slice(rootPath ? rootPath.split('/').filter(Boolean).length : 0).map((part, i) => {
                    const upTo = pathParts.slice(0, (rootPath ? rootPath.split('/').filter(Boolean).length : 0) + i + 1).join('/');
                    return (
                        <span key={upTo}>
                            <span style={{ color: 'var(--text-tertiary)' }}> / </span>
                            <span
                                style={{ cursor: 'pointer', color: 'var(--accent-primary)' }}
                                onClick={() => { setCurrentPath(upTo); setViewing(null); setEditing(false); }}
                            >
                                {part}
                            </span>
                        </span>
                    );
                })}
            </div>
        );
    };

    // ─── Toast ─────────────────────────────────────────

    const renderToast = () => {
        if (!toast) return null;
        return (
            <div style={{
                position: 'fixed', top: '20px', right: '20px', zIndex: 20000, padding: '12px 20px', borderRadius: '8px',
                background: toast.type === 'success' ? 'rgba(34, 197, 94, 0.9)' : 'rgba(239, 68, 68, 0.9)',
                color: '#fff', fontSize: '14px', fontWeight: 500, boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            }}>
                {toast.message}
            </div>
        );
    };

    // ─── Delete confirmation modal ────────────────────

    const renderDeleteModal = () => {
        if (!deleteTarget) return null;
        return (
            <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000 }}
                onClick={(e) => { if (e.target === e.currentTarget) setDeleteTarget(null); }}>
                <div style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '380px', border: '1px solid var(--border-subtle)', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                    <h4 style={{ marginBottom: '12px', fontSize: '15px' }}>{t('common.delete')}</h4>
                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px' }}>Delete "{deleteTarget.name}"?</p>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                        <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>{t('common.cancel')}</button>
                        <button className="btn btn-danger" onClick={handleDelete}>{t('common.delete')}</button>
                    </div>
                </div>
            </div>
        );
    };

    // ─── Prompt modal ─────────────────────────────────

    const renderPromptModal = () => {
        if (!promptModal) return null;
        return (
            <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000 }}
                onClick={(e) => { if (e.target === e.currentTarget) { setPromptModal(null); setPromptValue(''); } }}>
                <div style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '400px', border: '1px solid var(--border-subtle)', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                    <h4 style={{ marginBottom: '16px', fontSize: '15px' }}>{promptModal.title}</h4>
                    <input
                        className="form-input"
                        autoFocus
                        placeholder={promptModal.placeholder}
                        value={promptValue}
                        onChange={e => setPromptValue(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handlePromptConfirm(); }}
                        style={{ marginBottom: '16px' }}
                    />
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                        <button className="btn btn-secondary" onClick={() => { setPromptModal(null); setPromptValue(''); }}>{t('common.cancel')}</button>
                        <button className="btn btn-primary" onClick={handlePromptConfirm} disabled={!promptValue.trim()}>OK</button>
                    </div>
                </div>
            </div>
        );
    };

    // ═══════════════════════════════════════════════════
    // SINGLE FILE MODE (Soul-style)
    // ═══════════════════════════════════════════════════
    if (singleFile) {
        return (
            <div className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    {title ? <h3>{title}</h3> : <div />}
                    {edit && (
                        !editing ? (
                            <button className="btn btn-secondary" onClick={() => { setEditContent(content); setEditing(true); }}>{t('agent.soul.editButton')}</button>
                        ) : (
                            <div style={{ display: 'flex', gap: '8px' }}>
                                <button className="btn btn-secondary" onClick={() => setEditing(false)}>{t('common.cancel')}</button>
                                <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                                    {saving ? t('agent.soul.saving') : t('agent.soul.saveButton')}
                                </button>
                            </div>
                        )
                    )}
                </div>
                {editing ? (
                    <textarea ref={textareaRef} className="form-textarea" value={editContent} onChange={e => setEditContent(e.target.value)}
                        style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', lineHeight: '1.6', minHeight: '200px', resize: 'vertical', overflow: 'hidden' }} />
                ) : !contentLoaded ? (
                    <div style={{ padding: '20px', color: 'var(--text-tertiary)', textAlign: 'center' }}>{t('common.loading')}</div>
                ) : content ? (
                    singleFile?.endsWith('.md') ? (
                        <MarkdownRenderer content={content} style={{ padding: '4px 0' }} />
                    ) : (
                        <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)', fontSize: '13px', lineHeight: '1.6', margin: 0 }}>
                            {content}
                        </pre>
                    )
                ) : (
                    <div style={{ padding: '20px', color: 'var(--text-tertiary)', textAlign: 'center', fontSize: '13px' }}>
                        {t('common.noData', 'No content yet. Click Edit to add.')}
                    </div>
                )}
                {renderToast()}
            </div>
        );
    }

    // ═══════════════════════════════════════════════════
    // FILE VIEWER MODE (viewing a specific file)
    // ═══════════════════════════════════════════════════
    if (viewing) {
        const isText = isTextFile(viewing);
        return (
            <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                    <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '12px' }}
                        onClick={() => { setViewing(null); setEditing(false); }}>← {t('common.back')}</button>
                    <span style={{ fontSize: '12px', fontFamily: 'monospace', color: 'var(--text-secondary)', flex: 1 }}>{viewing}</span>
                    {isText && edit && (
                        !editing ? (
                            <button className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: '12px' }}
                                onClick={() => { setEditContent(content); setEditing(true); }}>✏️ {t('agent.soul.editButton')}</button>
                        ) : (
                            <div style={{ display: 'flex', gap: '6px' }}>
                                <button className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: '12px' }}
                                    onClick={() => setEditing(false)}>{t('common.cancel')}</button>
                                <button className="btn btn-primary" style={{ padding: '4px 12px', fontSize: '12px' }}
                                    disabled={saving} onClick={handleSave}>{saving ? 'Saving...' : t('common.save')}</button>
                            </div>
                        )
                    )}
                    {api.downloadUrl && (
                        <a href={api.downloadUrl(viewing)} download style={{ textDecoration: 'none' }}>
                            <button className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: '12px' }}>⬇ {t('common.download', 'Download')}</button>
                        </a>
                    )}
                    {canDelete && (
                        <button className="btn btn-danger" style={{ padding: '4px 10px', fontSize: '12px' }}
                            onClick={() => setDeleteTarget({ path: viewing, name: viewing.split('/').pop() || viewing })}>×</button>
                    )}
                </div>
                <div className="card">
                    {isText ? (
                        editing ? (
                            <textarea ref={textareaRef} className="form-textarea" value={editContent} onChange={e => setEditContent(e.target.value)}
                                style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', lineHeight: '1.6', minHeight: '200px', resize: 'vertical', overflow: 'hidden' }} />
                        ) : viewing?.endsWith('.md') ? (
                            <MarkdownRenderer content={content || ''} style={{ padding: '4px' }} />
                        ) : (
                            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)', fontSize: '12px', lineHeight: '1.5', margin: 0 }}>
                                {content || t('common.noData', 'No content yet')}
                            </pre>
                        )
                    ) : (
                        <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                            <div style={{ fontSize: '48px', marginBottom: '12px' }}>⌇</div>
                            <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '4px' }}>{viewing.split('/').pop()}</div>
                            <div style={{ fontSize: '12px', marginBottom: '16px' }}>Binary file — cannot preview</div>
                            {api.downloadUrl && (
                                <a href={api.downloadUrl(viewing)} download style={{ textDecoration: 'none' }}>
                                    <button className="btn btn-primary" style={{ fontSize: '13px', padding: '8px 20px' }}>⬇ {t('common.download', 'Download')}</button>
                                </a>
                            )}
                        </div>
                    )}
                </div>
                {renderDeleteModal()}
                {renderToast()}
            </div>
        );
    }

    // ═══════════════════════════════════════════════════
    // FILE LIST / BROWSER MODE
    // ═══════════════════════════════════════════════════
    return (
        <div>
            {/* Toolbar */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', flexWrap: 'wrap', gap: '8px' }}>
                {title && <h3 style={{ margin: 0 }}>{title}</h3>}
                {renderBreadcrumbs()}
                <div style={{ display: 'flex', gap: '6px', marginLeft: 'auto' }}>
                    {upload && api.upload && (
                        <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={handleUpload}>⬆ Upload</button>
                    )}
                    {newFolder && (
                        <button className="btn btn-secondary" style={{ fontSize: '12px' }}
                            onClick={() => setPromptModal({ title: t('agent.workspace.newFolder'), placeholder: t('agent.workspace.newFolderName'), action: 'newFolder' })}>
                            📁 {t('agent.workspace.newFolder')}
                        </button>
                    )}
                    {newFile && !fileFilter && (
                        <button className="btn btn-primary" style={{ fontSize: '12px' }}
                            onClick={() => setPromptModal({ title: t('agent.workspace.newFile', 'New File'), placeholder: 'filename.md', action: 'newFile' })}>
                            + {t('agent.workspace.newFile', 'New File')}
                        </button>
                    )}
                    {newFile && fileFilter?.includes('.md') && (
                        <button className="btn btn-primary" style={{ fontSize: '12px' }}
                            onClick={() => setPromptModal({ title: 'New Skill', placeholder: 'skill-name', action: 'newSkill' })}>
                            + New Skill
                        </button>
                    )}
                </div>
            </div>

            {/* File list */}
            {loading ? (
                <div style={{ padding: '20px', color: 'var(--text-tertiary)', textAlign: 'center' }}>{t('common.loading')}</div>
            ) : uploadProgress ? (
                <div className="card" style={{ padding: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                        <span style={{ fontSize: '13px' }}>⬆</span>
                        <span style={{ fontSize: '13px', fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{uploadProgress.fileName}</span>
                        <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums' }}>{uploadProgress.percent}%</span>
                    </div>
                    <div style={{ height: '4px', borderRadius: '2px', background: 'var(--bg-tertiary)', overflow: 'hidden' }}>
                        <div style={{ height: '100%', borderRadius: '2px', background: 'var(--accent-primary)', width: `${uploadProgress.percent}%`, transition: 'width 0.15s ease' }} />
                    </div>
                </div>
            ) : files.length === 0 ? (
                <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                    {t('common.noData')}
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    {/* Back button for subdirectories */}
                    {directoryNavigation && currentPath !== rootPath && (
                        <div className="card" style={{ display: 'flex', alignItems: 'center', padding: '8px 12px', cursor: 'pointer', opacity: 0.7 }}
                            onClick={() => {
                                const parts = currentPath.split('/').filter(Boolean);
                                parts.pop();
                                setCurrentPath(parts.join('/') || rootPath);
                                setViewing(null);
                                setEditing(false);
                            }}>
                            <span style={{ fontSize: '13px' }}>↩ ..</span>
                        </div>
                    )}
                    {files.map((f) => (
                        <div key={f.name} className="card"
                            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', cursor: 'pointer' }}
                            onClick={() => {
                                if (f.is_dir && directoryNavigation) {
                                    setCurrentPath(f.path || `${currentPath}/${f.name}`);
                                    setViewing(null);
                                    setEditing(false);
                                } else if (!f.is_dir) {
                                    setViewing(f.path || `${currentPath}/${f.name}`);
                                    setEditing(false);
                                }
                            }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{f.is_dir ? '/' : '·'}</span>
                                <span style={{ fontWeight: 500, fontSize: '13px' }}>{fileFilter?.includes('.md') ? f.name.replace('.md', '') : f.name}</span>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                {f.size != null && <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{(f.size / 1024).toFixed(1)} KB</span>}
                                {!f.is_dir && api.downloadUrl && (
                                    <a href={api.downloadUrl(f.path || `${currentPath}/${f.name}`)} download
                                        onClick={(e) => e.stopPropagation()}
                                        title={t('common.download', 'Download')}
                                        style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--accent-primary)', textDecoration: 'none', borderRadius: '4px' }}>
                                        ⬇
                                    </a>
                                )}
                                {canDelete && !f.is_dir && (
                                    <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                        onClick={(e) => { e.stopPropagation(); setDeleteTarget({ path: f.path || `${currentPath}/${f.name}`, name: f.name }); }}>
                                        ×
                                    </button>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {renderDeleteModal()}
            {renderPromptModal()}
            {renderToast()}
        </div>
    );
}
