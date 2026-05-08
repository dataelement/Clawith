/**
 * Unified FileBrowser component
 * Replaces duplicated file browsing/editing logic across:
 * - Agent Workspace, Skills, Soul, Memory tabs
 * - Enterprise Knowledge Base
 * - Project Files
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { IconDownload, IconEdit, IconFolder, IconFolderPlus, IconUpload } from '@tabler/icons-react';
import MarkdownRenderer from './MarkdownRenderer';
import Toast from './Toast';
import { useDropZone } from '../hooks/useDropZone';
import { useToast } from '../hooks/useToast';
import {
    IconCornerLeftUp, IconPencil, IconCheck, IconX,
    IconArrowsDownUp, IconSparkles, IconTrash, IconChevronDown,
} from '@tabler/icons-react';
import { isTextFile, isImage, isPdf, classifyFile, fileTypeIcon } from '../utils/fileTypes';

// ─── Types ─────────────────────────────────────────────

export interface FileItem {
    name: string;
    path: string;
    is_dir: boolean;
    size?: number;
    /** ISO timestamp string. Optional — used for sort=modified only. */
    modified?: string;
    // Optional fields used by callers that carry richer metadata (e.g. project files).
    // FileBrowser itself ignores most of them; pass `renderFileMeta` to surface them in rows.
    mime_type?: string;
    created_by_type?: 'user' | 'agent';
    [key: string]: unknown;
}

export interface FileBrowserApi {
    list: (path: string) => Promise<FileItem[]>;
    read: (path: string) => Promise<{ content: string }>;
    write: (path: string, content: string) => Promise<any>;
    delete: (path: string) => Promise<any>;
    upload?: (file: File, path: string, onProgress?: (pct: number) => void) => Promise<any>;
    downloadUrl?: (path: string) => string;
    /** Move/rename a file or directory. dst is the FULL new path (e.g. "posts/draft.md").
     *  When omitted, drag-to-folder, rename, and bulk-move are disabled. */
    move?: (src_path: string, dst_path: string) => Promise<any>;
    /** Sweep empty subdirectories. Returns the list of paths removed.
     *  When omitted, the "Clean empty folders" toolbar button is hidden. */
    cleanupEmpty?: () => Promise<{ removed: string[] }>;
}

export interface ContextAction {
    label: string;
    onClick: () => void;
    icon?: React.ReactNode;
    /** When true, render a separator BEFORE this item in the menu. */
    divider?: boolean;
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
        /** Show per-row checkboxes + action bar for bulk delete / move. */
        multiSelect?: boolean;
        /** Show toolbar sort selector (Name / Modified / Size). */
        sort?: boolean;
    };
    fileFilter?: string[];
    singleFile?: string;
    uploadAccept?: string;
    title?: string;
    readOnly?: boolean;
    onRefresh?: () => void;
    /** Optional row decoration (e.g. "uploaded / agent-written" badges). */
    renderFileMeta?: (file: FileItem) => React.ReactNode;
    /** Per-file delete-button visibility. Returns false to hide. */
    canDeleteFile?: (file: FileItem) => boolean;
    /** Right-click context menu items per file. Empty list hides the menu. */
    contextActions?: (file: FileItem) => ContextAction[];
}

// ─── Sort helpers ──────────────────────────────────────

type SortBy = 'name' | 'modified' | 'size';
type SortDir = 'asc' | 'desc';

function sortFiles(files: FileItem[], by: SortBy, dir: SortDir): FileItem[] {
    const sign = dir === 'asc' ? 1 : -1;
    const cmpName = (a: FileItem, b: FileItem) => a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    return [...files].sort((a, b) => {
        // Always group directories above files, regardless of sort direction.
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        if (by === 'name') return sign * cmpName(a, b);
        if (by === 'size') {
            const av = a.size ?? 0;
            const bv = b.size ?? 0;
            if (av !== bv) return sign * (av - bv);
            return cmpName(a, b);
        }
        // modified
        const av = a.modified || '';
        const bv = b.modified || '';
        if (av !== bv) return sign * av.localeCompare(bv);
        return cmpName(a, b);
    });
}

const MULTI_DRAG_MIME = 'application/x-fb-multi-paths';

// ─── Component ─────────────────────────────────────────

export default function FileBrowser({
    api,
    rootPath = '',
    features = {},
    fileFilter,
    singleFile,
    uploadAccept = '.pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.json,.xml,.yaml,.yml,.js,.ts,.py,.html,.css,.sh,.log,.png,.jpg,.jpeg,.gif,.svg,.webp',
    title,
    readOnly = false,
    onRefresh,
    renderFileMeta,
    canDeleteFile,
    contextActions,
}: FileBrowserProps) {
    const { t } = useTranslation();
    const {
        upload = false,
        newFile = false,
        newFolder = false,
        edit = !readOnly,
        delete: canDelete = !readOnly,
        directoryNavigation = false,
        multiSelect = false,
        sort = false,
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
    const { toast, showToast } = useToast();
    const [deleteTarget, setDeleteTarget] = useState<{ path: string; name: string } | null>(null);
    const [bulkDeleteTargets, setBulkDeleteTargets] = useState<string[] | null>(null);
    const [promptModal, setPromptModal] = useState<{ title: string; placeholder: string; action: string } | null>(null);
    const [promptValue, setPromptValue] = useState('');
    const [uploadProgress, setUploadProgress] = useState<{ fileName: string; percent: number } | null>(null);
    const [draggingPath, setDraggingPath] = useState<string | null>(null);
    const [dragOverPath, setDragOverPath] = useState<string | null>(null);

    // Rename
    const [renamingPath, setRenamingPath] = useState<string | null>(null);
    const [renameValue, setRenameValue] = useState('');

    // Multi-select (path strings)
    const [selected, setSelected] = useState<Set<string>>(new Set());

    // Sort
    const [sortBy, setSortBy] = useState<SortBy>('name');
    const [sortDir, setSortDir] = useState<SortDir>('asc');
    const [showSortMenu, setShowSortMenu] = useState(false);

    // Right-click context menu
    const [contextMenu, setContextMenu] = useState<{ x: number; y: number; file: FileItem } | null>(null);

    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const renameInputRef = useRef<HTMLInputElement>(null);

    // Auto-resize textarea to match content height
    useEffect(() => {
        const el = textareaRef.current;
        if (el && editing) {
            el.style.height = 'auto';
            el.style.height = Math.max(200, el.scrollHeight) + 'px';
        }
    }, [editing, editContent]);

    // Focus rename input when entering rename mode
    useEffect(() => {
        if (renamingPath && renameInputRef.current) {
            renameInputRef.current.focus();
            renameInputRef.current.select();
        }
    }, [renamingPath]);

    // Close context menu on outside click / Esc
    useEffect(() => {
        if (!contextMenu) return;
        const close = () => setContextMenu(null);
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
        // Slight delay so the originating right-click event doesn't immediately dismiss it.
        const t = window.setTimeout(() => {
            window.addEventListener('click', close);
            window.addEventListener('keydown', onKey);
        }, 0);
        return () => {
            window.clearTimeout(t);
            window.removeEventListener('click', close);
            window.removeEventListener('keydown', onKey);
        };
    }, [contextMenu]);

    // Close sort menu on outside click
    useEffect(() => {
        if (!showSortMenu) return;
        const close = () => setShowSortMenu(false);
        const id = window.setTimeout(() => window.addEventListener('click', close), 0);
        return () => { window.clearTimeout(id); window.removeEventListener('click', close); };
    }, [showSortMenu]);

    // Reset selection on path change
    useEffect(() => { setSelected(new Set()); }, [currentPath]);

    // ─── Helpers ───────────────────────────────────────

    const reload = useCallback(async () => {
        if (singleFile) {
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

    const sortedFiles = useMemo(() => sortFiles(files, sortBy, sortDir), [files, sortBy, sortDir]);

    // ─── Drag-and-drop upload ─────────────────────
    const handleDroppedFiles = useCallback(async (files: File[]) => {
        if (!api.upload || files.length === 0) return;
        try {
            for (const file of files) {
                setUploadProgress({ fileName: file.name, percent: 0 });
                await api.upload(file, currentPath, (pct) => {
                    setUploadProgress({ fileName: file.name, percent: pct });
                });
            }
            setUploadProgress(null);
            reload();
            onRefresh?.();
            showToast(t('agent.upload.success', 'Upload successful'));
        } catch (err: any) {
            setUploadProgress(null);
            showToast(t('agent.upload.failed', 'Upload failed') + ': ' + (err.message || ''), 'error');
        }
    }, [api, currentPath, reload, onRefresh, showToast, t]);

    const { isDragging, dropZoneProps } = useDropZone({
        onDrop: handleDroppedFiles,
        disabled: !upload || !api.upload || !!singleFile || !!viewing || readOnly,
        accept: uploadAccept,
    });

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
            // Refresh the directory listing so mtime / size update without
            // requiring a manual page refresh once the user clicks Back.
            if (!singleFile) reload();
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

    const handleBulkDelete = async () => {
        const paths = bulkDeleteTargets;
        if (!paths || paths.length === 0) return;
        setBulkDeleteTargets(null);
        let ok = 0;
        let failed = 0;
        for (const p of paths) {
            try {
                await api.delete(p);
                ok++;
            } catch {
                failed++;
            }
        }
        setSelected(new Set());
        reload();
        onRefresh?.();
        if (failed === 0) showToast(t('common.bulkDeleteSuccess', '{{n}} item(s) deleted', { n: ok }));
        else showToast(t('common.bulkDeletePartial', '{{ok}} deleted, {{failed}} failed', { ok, failed }), 'error');
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

    const handleRenameSubmit = async () => {
        const newName = renameValue.trim();
        const target = renamingPath;
        if (!target || !newName || !api.move) {
            setRenamingPath(null);
            return;
        }
        if (newName.includes('/') || newName === '.' || newName === '..') {
            showToast(t('common.invalidName', 'Invalid name'), 'error');
            return;
        }
        const parts = target.split('/');
        const oldName = parts[parts.length - 1];
        if (newName === oldName) {
            setRenamingPath(null);
            return;
        }
        const newPath = [...parts.slice(0, -1), newName].join('/');
        try {
            await api.move(target, newPath);
            setRenamingPath(null);
            reload();
            onRefresh?.();
            showToast(t('common.renamed', 'Renamed'));
        } catch (err: any) {
            showToast(t('common.renameFailed', 'Rename failed') + ': ' + (err?.message || ''), 'error');
        }
    };

    const handleCleanupEmpty = async () => {
        if (!api.cleanupEmpty) return;
        try {
            const r = await api.cleanupEmpty();
            const n = r.removed?.length || 0;
            if (n === 0) showToast(t('common.noEmptyFolders', 'No empty folders'));
            else showToast(t('common.emptyFoldersCleaned', '{{n}} empty folder(s) removed', { n }));
            reload();
            onRefresh?.();
        } catch (err: any) {
            showToast(t('common.cleanupFailed', 'Cleanup failed') + ': ' + (err?.message || ''), 'error');
        }
    };

    const handleMove = async (src: string, dst: string) => {
        if (!api.move) return;
        try {
            await api.move(src, dst);
            showToast(t('agent.move.success', 'Moved'));
        } catch (err: any) {
            showToast(t('agent.move.failed', 'Move failed') + ': ' + (err?.message || ''), 'error');
            throw err;
        }
    };

    const handleDropToFolder = async (e: React.DragEvent, targetDir: string) => {
        e.preventDefault();
        setDragOverPath(null);
        const multi = e.dataTransfer.getData(MULTI_DRAG_MIME);
        const sources = multi
            ? multi.split('\n').filter(Boolean)
            : [e.dataTransfer.getData('text/plain')].filter(Boolean);
        if (sources.length === 0 || !api.move) return;
        let moved = 0;
        const conflicts: string[] = [];
        const errors: string[] = [];
        for (const src of sources) {
            const filename = src.split('/').pop() || src;
            const dst = targetDir ? `${targetDir}/${filename}` : filename;
            if (src === dst) continue;
            // Refuse moving a folder into its descendant on the client too.
            if (src && dst.startsWith(src + '/')) {
                errors.push(`${filename}: cannot move into itself`);
                continue;
            }
            try {
                await api.move(src, dst);
                moved++;
            } catch (err: any) {
                if (err?.status === 409) conflicts.push(filename);
                else errors.push(`${filename}: ${err?.message || 'move failed'}`);
            }
        }
        setSelected(new Set());
        reload();
        onRefresh?.();
        // Surface results — never silent. Conflicts get a dedicated message
        // because they're the most common (target already has a file with that name).
        if (conflicts.length > 0) {
            const where = targetDir || t('common.root', 'root');
            const list = conflicts.slice(0, 3).join(', ');
            const extra = conflicts.length > 3 ? `, +${conflicts.length - 3}` : '';
            showToast(
                t('common.moveConflict', 'Already in {{where}}: {{names}}{{extra}}', { where, names: list, extra }),
                'error',
            );
        } else if (errors.length > 0) {
            showToast(errors[0], 'error');
        } else if (moved > 0) {
            showToast(t('agent.move.successN', '{{n}} moved', { n: moved }));
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
                    <IconFolder size={14} stroke={1.8} /> {rootPath || 'root'}
                </span>
                {pathParts.slice(rootPath ? rootPath.split('/').filter(Boolean).length : 0).map((part, i) => {
                    const upTo = pathParts.slice(0, (rootPath ? rootPath.split('/').filter(Boolean).length : 0) + i + 1).join('/');
                    return (
                        <span key={upTo}
                            onDragOver={api.move ? (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOverPath(`bc:${upTo}`); } : undefined}
                            onDragLeave={api.move ? () => setDragOverPath(null) : undefined}
                            onDrop={api.move ? (e) => handleDropToFolder(e, upTo) : undefined}
                            style={{
                                background: dragOverPath === `bc:${upTo}` ? 'var(--bg-elevated)' : undefined,
                                outline: dragOverPath === `bc:${upTo}` ? '2px dashed var(--accent-primary)' : undefined,
                                borderRadius: 4,
                            }}
                        >
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

    const renderToast = () => <Toast toast={toast} />;

    // ─── Modals ───────────────────────────────────────

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

    const renderBulkDeleteModal = () => {
        if (!bulkDeleteTargets) return null;
        return (
            <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000 }}
                onClick={(e) => { if (e.target === e.currentTarget) setBulkDeleteTargets(null); }}>
                <div style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '420px', border: '1px solid var(--border-subtle)', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                    <h4 style={{ marginBottom: '12px', fontSize: '15px' }}>{t('common.bulkDeleteTitle', 'Delete {{n}} item(s)?', { n: bulkDeleteTargets.length })}</h4>
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '20px', maxHeight: '160px', overflow: 'auto', fontFamily: 'monospace' }}>
                        {bulkDeleteTargets.slice(0, 10).map(p => <div key={p}>{p}</div>)}
                        {bulkDeleteTargets.length > 10 && <div>… +{bulkDeleteTargets.length - 10} more</div>}
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                        <button className="btn btn-secondary" onClick={() => setBulkDeleteTargets(null)}>{t('common.cancel')}</button>
                        <button className="btn btn-danger" onClick={handleBulkDelete}>{t('common.delete')}</button>
                    </div>
                </div>
            </div>
        );
    };

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

    // ─── Context menu ─────────────────────────────────

    const renderContextMenu = () => {
        if (!contextMenu) return null;
        const actions = contextActions ? contextActions(contextMenu.file) : [];
        if (actions.length === 0) return null;
        return (
            <div
                onClick={e => e.stopPropagation()}
                style={{
                    position: 'fixed', top: contextMenu.y, left: contextMenu.x,
                    background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)',
                    borderRadius: 8, boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
                    minWidth: 180, padding: '4px 0', zIndex: 10001,
                }}
            >
                {actions.map((a, idx) => (
                    <React.Fragment key={idx}>
                        {a.divider && <div style={{ height: 1, background: 'var(--border-subtle)', margin: '4px 0' }} />}
                        <div
                            onClick={() => { setContextMenu(null); a.onClick(); }}
                            style={{
                                padding: '8px 14px', fontSize: 13, cursor: 'pointer',
                                display: 'flex', alignItems: 'center', gap: 8,
                            }}
                            onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                        >
                            {a.icon}
                            <span>{a.label}</span>
                        </div>
                    </React.Fragment>
                ))}
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
                                onClick={() => { setEditContent(content); setEditing(true); }}><IconEdit size={13} stroke={1.8} /> {t('agent.soul.editButton')}</button>
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
                            <button className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: '12px' }}><IconDownload size={13} stroke={1.8} /> {t('common.download', 'Download')}</button>
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
                    ) : isImage(viewing) ? (
                        <div style={{ textAlign: 'center', padding: '20px', background: 'var(--bg-tertiary)', borderRadius: '8px' }}>
                            {api.downloadUrl ? (
                                <img
                                    src={api.downloadUrl(viewing)}
                                    alt={viewing.split('/').pop()}
                                    style={{ maxWidth: '100%', maxHeight: '600px', objectFit: 'contain', borderRadius: '4px', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}
                                />
                            ) : (
                                <div style={{ padding: '20px', color: 'var(--text-tertiary)' }}>Cannot preview image without download URL</div>
                            )}
                        </div>
                    ) : isPdf(viewing) && api.downloadUrl ? (
                        <iframe
                            src={api.downloadUrl(viewing)}
                            title={viewing.split('/').pop()}
                            style={{ width: '100%', height: '70vh', border: 'none', borderRadius: '4px', background: 'var(--bg-tertiary)' }}
                        />
                    ) : (
                        <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                            <div style={{ fontSize: '48px', marginBottom: '12px' }}>⌇</div>
                            <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '4px' }}>{viewing.split('/').pop()}</div>
                            <div style={{ fontSize: '12px', marginBottom: '16px' }}>Binary file — cannot preview</div>
                            {api.downloadUrl && (
                                <a href={api.downloadUrl(viewing)} download style={{ textDecoration: 'none' }}>
                                    <button className="btn btn-primary" style={{ fontSize: '13px', padding: '8px 20px' }}><IconDownload size={14} stroke={1.8} /> {t('common.download', 'Download')}</button>
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

    // For multi-select: select-all checkbox state
    const selectablePaths = sortedFiles.map(f => f.path || `${currentPath}/${f.name}`);
    const allSelected = multiSelect && selectablePaths.length > 0 && selectablePaths.every(p => selected.has(p));
    const someSelected = multiSelect && selected.size > 0;

    const toggleSelect = (path: string) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };

    const toggleSelectAll = () => {
        if (allSelected) setSelected(new Set());
        else setSelected(new Set(selectablePaths));
    };

    return (
        <div className="drop-zone-wrapper" {...dropZoneProps}>
            {/* Drop overlay */}
            {isDragging && (
                <div className="drop-zone-overlay">
                    <div className="drop-zone-overlay__icon"><IconUpload size={28} stroke={1.8} /></div>
                    <div className="drop-zone-overlay__text">{t('agent.workspace.dragOrClick', 'Drop files to upload')}</div>
                </div>
            )}

            {/* Toolbar */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', flexWrap: 'wrap', gap: '8px' }}>
                {title && <h3 style={{ margin: 0 }}>{title}</h3>}
                {renderBreadcrumbs()}
                <div style={{ display: 'flex', gap: '6px', marginLeft: 'auto', alignItems: 'center' }}>
                    {sort && (
                        <div style={{ position: 'relative' }} onClick={e => e.stopPropagation()}>
                            <button className="btn btn-secondary" style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: 4 }}
                                onClick={() => setShowSortMenu(v => !v)}
                                title={t('common.sort', 'Sort')}>
                                <IconArrowsDownUp size={13} stroke={1.75} />
                                <span>
                                    {sortBy === 'name' && t('common.sortName', 'Name')}
                                    {sortBy === 'modified' && t('common.sortModified', 'Modified')}
                                    {sortBy === 'size' && t('common.sortSize', 'Size')}
                                    {' '}{sortDir === 'asc' ? '↑' : '↓'}
                                </span>
                                <IconChevronDown size={11} stroke={1.75} />
                            </button>
                            {showSortMenu && (
                                <div style={{
                                    position: 'absolute', top: 'calc(100% + 4px)', right: 0,
                                    background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)',
                                    borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                                    minWidth: 160, padding: '4px 0', zIndex: 100,
                                }}>
                                    {(['name', 'modified', 'size'] as SortBy[]).map(by => (
                                        <div key={by}
                                            onClick={() => {
                                                if (sortBy === by) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
                                                else { setSortBy(by); setSortDir('asc'); }
                                                setShowSortMenu(false);
                                            }}
                                            style={{
                                                padding: '6px 14px', fontSize: 12, cursor: 'pointer',
                                                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                                fontWeight: sortBy === by ? 600 : 400,
                                            }}
                                            onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                                            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                                        >
                                            <span>
                                                {by === 'name' && t('common.sortName', 'Name')}
                                                {by === 'modified' && t('common.sortModified', 'Modified')}
                                                {by === 'size' && t('common.sortSize', 'Size')}
                                            </span>
                                            {sortBy === by && <span>{sortDir === 'asc' ? '↑' : '↓'}</span>}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                    {api.cleanupEmpty && !readOnly && (
                        <button className="btn btn-secondary" style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: 4 }}
                            onClick={handleCleanupEmpty}
                            title={t('common.cleanupEmptyHint', 'Remove empty subdirectories')}>
                            <IconSparkles size={13} stroke={1.75} />
                            {t('common.cleanupEmpty', 'Clean empty')}
                        </button>
                    )}
                    {upload && api.upload && (
                        <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={handleUpload}><IconUpload size={13} stroke={1.8} /> Upload</button>
                    )}
                    {newFolder && (
                        <button className="btn btn-secondary" style={{ fontSize: '12px' }}
                            onClick={() => setPromptModal({ title: t('agent.workspace.newFolder'), placeholder: t('agent.workspace.newFolderName'), action: 'newFolder' })}>
                            <IconFolderPlus size={13} stroke={1.8} /> {t('agent.workspace.newFolder')}
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

            {/* Bulk-action bar (visible when at least one item selected) */}
            {someSelected && (
                <div className="card" style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '8px 12px', marginBottom: 8,
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--accent-primary)',
                }}>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>
                        {t('common.nSelected', '{{n}} selected', { n: selected.size })}
                    </span>
                    <span style={{ flex: 1 }} />
                    {api.move && (
                        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                            {t('common.dragToFolderHint', 'Drag any selected item onto a folder to move all')}
                        </span>
                    )}
                    <button className="btn btn-danger" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}
                        onClick={() => setBulkDeleteTargets(Array.from(selected))}>
                        <IconTrash size={13} stroke={1.75} />
                        {t('common.deleteN', 'Delete ({{n}})', { n: selected.size })}
                    </button>
                    <button className="btn btn-secondary" style={{ fontSize: 12 }}
                        onClick={() => setSelected(new Set())}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                </div>
            )}

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
            ) : sortedFiles.length === 0 ? (
                <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                    {upload && api.upload
                        ? t('agent.workspace.dragOrClick', 'Drop files here or click Upload')
                        : t('common.noData')}
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    {/* Optional select-all header */}
                    {multiSelect && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 14px', fontSize: 11, color: 'var(--text-tertiary)' }}>
                            <input
                                type="checkbox"
                                checked={allSelected}
                                onChange={toggleSelectAll}
                                style={{ cursor: 'pointer', width: 14, height: 14, margin: 0 }}
                                title={allSelected ? t('common.deselectAll', 'Deselect all') : t('common.selectAll', 'Select all')}
                            />
                            <span>{t('common.selectAll', 'Select all')}</span>
                        </div>
                    )}

                    {/* Back-to-parent row */}
                    {directoryNavigation && currentPath !== rootPath && (
                        <div className="card"
                            onDragOver={api.move ? (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOverPath('..'); } : undefined}
                            onDragLeave={api.move ? () => setDragOverPath(null) : undefined}
                            onDrop={api.move ? (e) => {
                                const parts = currentPath.split('/').filter(Boolean);
                                parts.pop();
                                const parentDir = parts.join('/') || rootPath;
                                handleDropToFolder(e, parentDir);
                            } : undefined}
                            style={{
                                display: 'flex', alignItems: 'center', gap: '8px',
                                padding: '8px 12px', cursor: 'pointer',
                                opacity: dragOverPath === '..' ? 1 : 0.65,
                                background: dragOverPath === '..' ? 'var(--bg-elevated)' : undefined,
                                outline: dragOverPath === '..' ? '2px dashed var(--accent-primary)' : undefined,
                            }}
                            onClick={() => {
                                const parts = currentPath.split('/').filter(Boolean);
                                parts.pop();
                                setCurrentPath(parts.join('/') || rootPath);
                                setViewing(null);
                                setEditing(false);
                            }}>
                            <IconCornerLeftUp size={15} stroke={1.5} style={{ color: 'var(--text-tertiary)' }} />
                            <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>..</span>
                        </div>
                    )}
                    {sortedFiles.map((f) => {
                        const showDelete = canDelete && (canDeleteFile ? canDeleteFile(f) : true);
                        const fullPath = f.path || `${currentPath}/${f.name}`;
                        const Icon = f.is_dir ? IconFolder : fileTypeIcon(classifyFile(f.name, f.mime_type));
                        const isDropTarget = api.move && f.is_dir && dragOverPath === fullPath;
                        const isBeingDragged = draggingPath === fullPath;
                        const isSelected = selected.has(fullPath);
                        const isRenaming = renamingPath === fullPath;
                        return (
                            <div key={f.name} className="card"
                                draggable={!!api.move && !readOnly && !isRenaming}
                                onDragStart={api.move ? (e) => {
                                    // If this row is in the selection, drag the whole selection.
                                    if (selected.has(fullPath) && selected.size > 1) {
                                        e.dataTransfer.setData(MULTI_DRAG_MIME, Array.from(selected).join('\n'));
                                    } else {
                                        e.dataTransfer.setData('text/plain', fullPath);
                                    }
                                    e.dataTransfer.effectAllowed = 'move';
                                    setDraggingPath(fullPath);
                                } : undefined}
                                onDragEnd={api.move ? () => { setDraggingPath(null); setDragOverPath(null); } : undefined}
                                onDragOver={api.move && f.is_dir ? (e) => {
                                    e.preventDefault();
                                    e.dataTransfer.dropEffect = 'move';
                                    setDragOverPath(fullPath);
                                } : undefined}
                                onDragLeave={api.move && f.is_dir ? () => setDragOverPath(null) : undefined}
                                onDrop={api.move && f.is_dir ? (e) => handleDropToFolder(e, fullPath) : undefined}
                                onContextMenu={contextActions ? (e) => {
                                    const acts = contextActions(f);
                                    if (acts.length === 0) return;
                                    e.preventDefault();
                                    setContextMenu({ x: e.clientX, y: e.clientY, file: f });
                                } : undefined}
                                style={{
                                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                    padding: '10px 12px', cursor: isRenaming ? 'default' : 'pointer',
                                    opacity: isBeingDragged ? 0.4 : 1,
                                    background: isDropTarget ? 'var(--bg-elevated)' : (isSelected ? 'var(--bg-elevated)' : undefined),
                                    outline: isDropTarget ? '2px dashed var(--accent-primary)' : undefined,
                                    borderLeft: isSelected ? '3px solid var(--accent-primary)' : '3px solid transparent',
                                    transition: 'background 0.1s, outline 0.1s',
                                }}
                                onClick={() => {
                                    if (isRenaming) return;
                                    if (f.is_dir && directoryNavigation) {
                                        setCurrentPath(fullPath);
                                        setViewing(null);
                                        setEditing(false);
                                    } else if (!f.is_dir) {
                                        setViewing(fullPath);
                                        setEditing(false);
                                    }
                                }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0, flex: 1 }}>
                                    {multiSelect && (
                                        <input
                                            type="checkbox"
                                            checked={isSelected}
                                            onClick={(e) => e.stopPropagation()}
                                            onChange={() => toggleSelect(fullPath)}
                                            style={{ cursor: 'pointer', width: 14, height: 14, margin: 0, flexShrink: 0 }}
                                        />
                                    )}
                                    <Icon
                                        size={16}
                                        stroke={f.is_dir ? 1.75 : 1.5}
                                        style={{
                                            color: f.is_dir ? 'var(--accent-primary)' : 'var(--text-secondary)',
                                            flexShrink: 0,
                                        }}
                                    />
                                    {isRenaming ? (
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1 }} onClick={e => e.stopPropagation()}>
                                            <input
                                                ref={renameInputRef}
                                                className="form-input"
                                                value={renameValue}
                                                onChange={e => setRenameValue(e.target.value)}
                                                onKeyDown={e => {
                                                    if (e.key === 'Enter') handleRenameSubmit();
                                                    else if (e.key === 'Escape') setRenamingPath(null);
                                                }}
                                                style={{ fontSize: 13, padding: '4px 8px', height: 28, flex: 1 }}
                                            />
                                            <button className="btn btn-ghost" style={{ padding: '2px 6px' }}
                                                onClick={(e) => { e.stopPropagation(); handleRenameSubmit(); }}
                                                title={t('common.save', 'Save')}>
                                                <IconCheck size={14} stroke={1.75} style={{ color: 'var(--success)' }} />
                                            </button>
                                            <button className="btn btn-ghost" style={{ padding: '2px 6px' }}
                                                onClick={(e) => { e.stopPropagation(); setRenamingPath(null); }}
                                                title={t('common.cancel', 'Cancel')}>
                                                <IconX size={14} stroke={1.75} style={{ color: 'var(--text-tertiary)' }} />
                                            </button>
                                        </div>
                                    ) : (
                                        <span style={{
                                            fontWeight: f.is_dir ? 600 : 500,
                                            fontSize: '13px',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                            whiteSpace: 'nowrap',
                                        }}>
                                            {fileFilter?.includes('.md') ? f.name.replace('.md', '') : f.name}
                                            {f.is_dir && '/'}
                                        </span>
                                    )}
                                </div>
                                {!isRenaming && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        {renderFileMeta && renderFileMeta(f)}
                                        {f.size != null && !f.is_dir && <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{(f.size / 1024).toFixed(1)} KB</span>}
                                        {!f.is_dir && api.downloadUrl && (
                                            <a href={api.downloadUrl(fullPath)} download
                                                onClick={(e) => e.stopPropagation()}
                                                title={t('common.download', 'Download')}
                                                style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--accent-primary)', textDecoration: 'none', borderRadius: '4px' }}>
                                                ⬇
                                            </a>
                                        )}
                                        {api.move && !readOnly && (
                                            <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px' }}
                                                onClick={(e) => { e.stopPropagation(); setRenameValue(f.name); setRenamingPath(fullPath); }}
                                                title={t('common.rename', 'Rename')}>
                                                <IconPencil size={13} stroke={1.5} style={{ color: 'var(--text-tertiary)' }} />
                                            </button>
                                        )}
                                        {showDelete && (
                                            <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                                onClick={(e) => { e.stopPropagation(); setDeleteTarget({ path: fullPath, name: f.name }); }}>
                                                ×
                                            </button>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {renderDeleteModal()}
            {renderBulkDeleteModal()}
            {renderPromptModal()}
            {renderContextMenu()}
            {renderToast()}
        </div>
    );
}
