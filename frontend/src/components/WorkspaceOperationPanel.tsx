import { useEffect, useMemo, useRef, useState } from 'react';
import MarkdownRenderer from './MarkdownRenderer';
import { fileApi } from '../services/api';

export interface WorkspaceActivity {
    action: 'write' | 'edit' | 'convert' | 'delete';
    path: string;
    tool?: string;
    ok?: boolean;
}

export interface WorkspaceLiveDraft {
    id: string;
    action: 'write' | 'edit' | 'convert' | 'delete';
    tool: string;
    path?: string;
    content?: string;
    status: 'drafting' | 'running';
}

interface WorkspaceFileNode {
    name: string;
    path: string;
    is_dir: boolean;
    children?: WorkspaceFileNode[];
}

interface Props {
    agentId: string;
    sessionId?: string;
    activePath?: string | null;
    activities: WorkspaceActivity[];
    liveDraft?: WorkspaceLiveDraft | null;
    onSelectPath: (path: string) => void;
    onEditingChange?: (editing: boolean) => void;
}

const WORKSPACE_ROOT = 'workspace';
const EDITABLE_EXTS = new Set(['.md', '.markdown', '.csv']);
const PREVIEW_EXTS = new Set(['.md', '.markdown', '.csv', '.html', '.htm', '.pdf', '.xlsx', '.docx', '.pptx', '.txt']);
const MIN_SAVING_VISIBLE_MS = 650;
const SAVED_VISIBLE_MS = 1600;

function extOf(path: string): string {
    const idx = path.lastIndexOf('.');
    return idx >= 0 ? path.slice(idx).toLowerCase() : '';
}

function parseCsv(text: string): string[][] {
    const rows: string[][] = [];
    let row: string[] = [];
    let cell = '';
    let quoted = false;
    for (let i = 0; i < text.length; i++) {
        const ch = text[i];
        const next = text[i + 1];
        if (ch === '"' && quoted && next === '"') {
            cell += '"';
            i++;
        } else if (ch === '"') {
            quoted = !quoted;
        } else if (ch === ',' && !quoted) {
            row.push(cell);
            cell = '';
        } else if ((ch === '\n' || ch === '\r') && !quoted) {
            if (ch === '\r' && next === '\n') i++;
            row.push(cell);
            rows.push(row);
            row = [];
            cell = '';
        } else {
            cell += ch;
        }
    }
    if (cell || row.length) {
        row.push(cell);
        rows.push(row);
    }
    return rows;
}

function fileName(path: string): string {
    return path.split('/').pop() || path;
}

function isPreviewable(path: string): boolean {
    return PREVIEW_EXTS.has(extOf(path));
}

function parentDirs(path?: string | null): string[] {
    if (!path || !path.startsWith(`${WORKSPACE_ROOT}/`)) return [WORKSPACE_ROOT];
    const parts = path.split('/');
    const dirs: string[] = [WORKSPACE_ROOT];
    for (let i = 1; i < parts.length - 1; i += 1) {
        dirs.push(parts.slice(0, i + 1).join('/'));
    }
    return dirs;
}

function HtmlPreviewFrame({ content, title }: { content: string; title: string }) {
    const viewportRef = useRef<HTMLDivElement>(null);
    const frameRef = useRef<HTMLIFrameElement>(null);
    const [frameStyle, setFrameStyle] = useState({
        width: '100%',
        height: '100%',
        scaledWidth: '100%',
        scaledHeight: '100%',
        scale: 1,
    });

    const fitFrame = () => {
        const viewport = viewportRef.current;
        const frame = frameRef.current;
        const doc = frame?.contentDocument;
        if (!viewport || !frame || !doc?.body) return;

        const body = doc.body;
        const root = doc.documentElement;
        body.style.margin = body.style.margin || '0';
        root.style.margin = root.style.margin || '0';
        body.style.overflow = 'hidden';
        root.style.overflow = 'hidden';

        const contentWidth = Math.max(root.scrollWidth, body.scrollWidth, body.offsetWidth, 1);
        const contentHeight = Math.max(root.scrollHeight, body.scrollHeight, body.offsetHeight, 1);
        const availableWidth = Math.max(viewport.clientWidth - 18, 1);
        const scale = Math.min(1, availableWidth / contentWidth);

        setFrameStyle({
            width: `${contentWidth}px`,
            height: `${contentHeight}px`,
            scaledWidth: `${contentWidth * scale}px`,
            scaledHeight: `${contentHeight * scale}px`,
            scale,
        });
    };

    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport || typeof ResizeObserver === 'undefined') return;
        const observer = new ResizeObserver(() => fitFrame());
        observer.observe(viewport);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        setFrameStyle({ width: '100%', height: '100%', scaledWidth: '100%', scaledHeight: '100%', scale: 1 });
    }, [content]);

    return (
        <div className="workspace-op-html-fit" ref={viewportRef}>
            <div
                className="workspace-op-html-fit-inner"
                style={{
                    width: frameStyle.scaledWidth,
                    height: frameStyle.scaledHeight,
                }}
            >
                <iframe
                    ref={frameRef}
                    sandbox="allow-same-origin"
                    srcDoc={content}
                    title={title}
                    onLoad={() => {
                        requestAnimationFrame(() => {
                            fitFrame();
                            requestAnimationFrame(fitFrame);
                        });
                    }}
                    style={{
                        width: frameStyle.width,
                        height: frameStyle.height,
                        transform: `scale(${frameStyle.scale})`,
                    }}
                />
            </div>
        </div>
    );
}

export default function WorkspaceOperationPanel({
    agentId,
    sessionId,
    activePath,
    activities,
    liveDraft,
    onSelectPath,
    onEditingChange,
}: Props) {
    const [preview, setPreview] = useState<any>(null);
    const [content, setContent] = useState('');
    const [draft, setDraft] = useState('');
    const [editing, setEditing] = useState(false);
    const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
    const [revisions, setRevisions] = useState<any[]>([]);
    const [fileTree, setFileTree] = useState<WorkspaceFileNode[]>([]);
    const [activityOpen, setActivityOpen] = useState(false);
    const [treeOpen, setTreeOpen] = useState(true);
    const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => new Set([WORKSPACE_ROOT]));
    const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const saveStateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const lockTimer = useRef<ReturnType<typeof setInterval> | null>(null);

    const ext = activePath ? extOf(activePath) : '';
    const canEdit = !!activePath && EDITABLE_EXTS.has(ext);
    const isHtml = ext === '.html' || ext === '.htm';
    const activityKey = activities.map((item) => `${item.action}:${item.path}`).join('|');

    const load = async () => {
        if (!activePath) return;
        const data = await fileApi.preview(agentId, activePath);
        setPreview(data);
        const text = data.content || '';
        setContent(text);
        setDraft(text);
        setRevisions(await fileApi.revisions(agentId, activePath).catch(() => []));
    };

    const loadFileTree = async () => {
        const loadDir = async (path: string, depth: number): Promise<WorkspaceFileNode[]> => {
            if (depth > 4) return [];
            const items = await fileApi.list(agentId, path).catch(() => []);
            const visible = items.filter((item: WorkspaceFileNode) => item.is_dir || isPreviewable(item.path));
            return Promise.all(visible.map(async (item: WorkspaceFileNode) => {
                if (!item.is_dir) return item;
                return { ...item, children: await loadDir(item.path, depth + 1) };
            }));
        };
        const roots = await loadDir(WORKSPACE_ROOT, 0);
        setFileTree(roots);
    };

    useEffect(() => {
        setEditing(false);
        onEditingChange?.(false);
        if (liveDraft && (!activePath || !liveDraft.path || liveDraft.path === activePath)) {
            setPreview(null);
            setContent(liveDraft.content || '');
            setDraft(liveDraft.content || '');
            return;
        }
        load().catch(() => {
            setPreview(null);
            setContent('');
            setDraft('');
        });
    }, [agentId, activePath, liveDraft?.id, liveDraft?.path]);

    useEffect(() => {
        loadFileTree();
    }, [agentId, activityKey, liveDraft?.path]);

    useEffect(() => {
        const pathToReveal = activePath || liveDraft?.path;
        const dirs = parentDirs(pathToReveal);
        setExpandedDirs((prev) => {
            const next = new Set(prev);
            dirs.forEach((dir) => next.add(dir));
            return next;
        });
    }, [activePath, liveDraft?.path]);

    useEffect(() => {
        onEditingChange?.(editing);
        if (!activePath || !editing) return;
        fileApi.lock(agentId, activePath, sessionId).catch(() => {});
        lockTimer.current = setInterval(() => {
            fileApi.lock(agentId, activePath, sessionId).catch(() => {});
        }, 30_000);
        return () => {
            if (lockTimer.current) clearInterval(lockTimer.current);
            fileApi.unlock(agentId, activePath).catch(() => {});
        };
    }, [agentId, activePath, editing, sessionId]);

    const clearSaveStateTimer = () => {
        if (saveStateTimer.current) {
            clearTimeout(saveStateTimer.current);
            saveStateTimer.current = null;
        }
    };

    const runAutosaveWithFeedback = async (nextContent: string) => {
        if (!activePath) return;
        clearSaveStateTimer();
        const startedAt = Date.now();
        setSaveState('saving');
        try {
            await fileApi.autosave(agentId, activePath, nextContent, sessionId);
            const remainingSavingMs = Math.max(0, MIN_SAVING_VISIBLE_MS - (Date.now() - startedAt));
            await new Promise((resolve) => setTimeout(resolve, remainingSavingMs));
            setContent(nextContent);
            setSaveState('saved');
            setRevisions(await fileApi.revisions(agentId, activePath).catch(() => []));
            clearSaveStateTimer();
            saveStateTimer.current = setTimeout(() => {
                setSaveState('idle');
                saveStateTimer.current = null;
            }, SAVED_VISIBLE_MS);
        } catch {
            setSaveState('error');
        }
    };

    useEffect(() => {
        return () => clearSaveStateTimer();
    }, []);

    useEffect(() => {
        if (!editing || !activePath || draft === content) return;
        if (saveTimer.current) clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(async () => {
            await runAutosaveWithFeedback(draft);
        }, 900);
        return () => {
            if (saveTimer.current) clearTimeout(saveTimer.current);
        };
    }, [agentId, activePath, draft, content, editing, sessionId]);

    const previewType = preview?.type || preview?.kind;

    const csvRows = useMemo(() => {
        if (previewType === 'csv') return parseCsv(editing ? draft : content).slice(0, 200);
        return [];
    }, [previewType, content, draft, editing]);

    const xlsxRows = previewType === 'xlsx' ? (preview.sheets?.[0]?.rows || []) : [];

    const finishEditing = async () => {
        if (saveTimer.current) clearTimeout(saveTimer.current);
        if (activePath && draft !== content) {
            await runAutosaveWithFeedback(draft);
        }
        setEditing(false);
        onEditingChange?.(false);
        if (activePath) {
            await fileApi.unlock(agentId, activePath).catch(() => {});
            setRevisions(await fileApi.revisions(agentId, activePath).catch(() => []));
        }
    };

    const restore = async (revisionId: string) => {
        if (!activePath) return;
        await fileApi.restoreRevision(agentId, revisionId);
        await load();
    };

    const renderPreview = () => {
        const draftMatchesActiveFile = liveDraft?.path && activePath && liveDraft.path === activePath;
        const shouldRenderLiveDraft = !!liveDraft && (!activePath || !liveDraft.path || draftMatchesActiveFile);
        if (shouldRenderLiveDraft) {
            const draftExt = liveDraft.path ? extOf(liveDraft.path) : ext;
            const draftContent = liveDraft.content || '';
            if (draftExt === '.html' || draftExt === '.htm') {
                return (
                    <div className="workspace-op-live">
                        <div className="workspace-op-live-banner">{liveDraft.status === 'drafting' ? 'Drafting HTML...' : 'Writing HTML...'}</div>
                        {draftContent ? (
                            <HtmlPreviewFrame content={draftContent} title={fileName(liveDraft.path || 'draft.html')} />
                        ) : (
                            <div className="workspace-op-empty">Preparing file content...</div>
                        )}
                    </div>
                );
            }
            if (draftExt === '.csv') {
                const rows = parseCsv(draftContent).slice(0, 200);
                return (
                    <div className="workspace-op-live">
                        <div className="workspace-op-live-banner">{liveDraft.status === 'drafting' ? 'Drafting CSV...' : 'Writing CSV...'}</div>
                        {rows.length ? (
                            <div className="workspace-op-table-wrap">
                                <table className="workspace-op-table">
                                    <tbody>
                                        {rows.map((row, i) => (
                                            <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <div className="workspace-op-empty">Preparing file content...</div>
                        )}
                    </div>
                );
            }
            return (
                <div className="workspace-op-live">
                    <div className="workspace-op-live-banner">{liveDraft.status === 'drafting' ? 'Drafting file...' : 'Writing file...'}</div>
                    {draftContent ? <MarkdownRenderer content={draftContent} /> : <div className="workspace-op-empty">Preparing file content...</div>}
                </div>
            );
        }
        if (!activePath) {
            return <div className="workspace-op-empty">No workspace file activity yet.</div>;
        }
        if (!preview) {
            return <div className="workspace-op-empty">Loading file preview...</div>;
        }
        if (editing) {
            return (
                <textarea
                    className="workspace-op-editor"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    spellCheck={false}
                />
            );
        }
        if (previewType === 'md' || previewType === 'markdown') {
            return <MarkdownRenderer content={content || ''} />;
        }
        if (previewType === 'csv') {
            return (
                <div className="workspace-op-table-wrap">
                    <table className="workspace-op-table">
                        <tbody>
                            {csvRows.map((row, i) => (
                                <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            );
        }
        if (previewType === 'xlsx') {
            return (
                <div className="workspace-op-table-wrap">
                    <table className="workspace-op-table">
                        <tbody>
                            {xlsxRows.map((row: string[], i: number) => (
                                <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            );
        }
        if (isHtml) {
            return <HtmlPreviewFrame content={content || ''} title={fileName(activePath)} />;
        }
        if (previewType === 'pdf') {
            return <iframe className="workspace-op-pdf" src={fileApi.downloadUrl(agentId, activePath, { inline: true })} title={fileName(activePath)} />;
        }
        if (previewType === 'docx') {
            return <pre className="workspace-op-text-preview">{preview.content || preview.text}</pre>;
        }
        if (previewType === 'pptx') {
            return (
                <div className="workspace-op-ppt-preview">
                    {(preview.slides || []).map((slide: any) => (
                        <section className="workspace-op-slide-card" key={slide.slide}>
                            <div className="workspace-op-slide-label">Slide {slide.slide}</div>
                            <div className="workspace-op-slide-canvas">
                                {(slide.shapes || []).map((shape: any, idx: number) => (
                                    <div
                                        key={idx}
                                        className="workspace-op-slide-shape"
                                        style={{
                                            left: `${Math.max(0, Math.min(1, shape.left || 0)) * 100}%`,
                                            top: `${Math.max(0, Math.min(1, shape.top || 0)) * 100}%`,
                                            width: `${Math.max(0.04, Math.min(1, shape.width || 0.5)) * 100}%`,
                                            height: `${Math.max(0.04, Math.min(1, shape.height || 0.15)) * 100}%`,
                                        }}
                                    >
                                        {shape.text}
                                    </div>
                                ))}
                            </div>
                        </section>
                    ))}
                </div>
            );
        }
        return <div className="workspace-op-empty">Preview is not available for this file. Download it instead.</div>;
    };

    const renderFileTreeNodes = (nodes: WorkspaceFileNode[], depth = 0) => nodes.map((node) => {
        const selected = node.path === activePath;
        if (node.is_dir) {
            const expanded = expandedDirs.has(node.path);
            return (
                <div key={node.path || node.name}>
                    <button
                        className="workspace-op-tree-dir"
                        onClick={() => setExpandedDirs((prev) => {
                            const next = new Set(prev);
                            if (next.has(node.path)) next.delete(node.path);
                            else next.add(node.path);
                            return next;
                        })}
                        style={{ paddingLeft: `${6 + depth * 12}px` }}
                    >
                        <span className="workspace-op-tree-chevron">{expanded ? '▾' : '▸'}</span>
                        <span>{node.name}</span>
                    </button>
                    {expanded && node.children && renderFileTreeNodes(node.children, depth + 1)}
                </div>
            );
        }
        return (
            <button
                key={node.path}
                className={`workspace-op-tree-file ${selected ? 'active' : ''}`}
                onClick={() => !editing && onSelectPath(node.path)}
                disabled={editing}
                title={node.path}
                style={{ paddingLeft: `${18 + depth * 12}px` }}
            >
                {node.name}
            </button>
        );
    });

    return (
        <div className="workspace-op">
            <div className="workspace-op-header">
                <div className="workspace-op-heading">
                    <div className="workspace-op-title">{activePath ? fileName(activePath) : liveDraft?.path ? fileName(liveDraft.path) : 'Workspace'}</div>
                </div>
                <div className="workspace-op-actions">
                    {saveState !== 'idle' && <span className={`workspace-op-save ${saveState}`}>{saveState}</span>}
                    <button className={`workspace-op-icon-btn ${activityOpen ? 'active' : ''}`} onClick={() => setActivityOpen((open) => !open)} title="Version history">◷</button>
                    {activePath && canEdit && !editing && <button className="workspace-op-icon-btn" onClick={() => setEditing(true)} title="Edit">✎</button>}
                    {editing && <button className="workspace-op-icon-btn active" onClick={finishEditing} title="Done">✓</button>}
                    {activePath && (
                        <a href={fileApi.downloadUrl(agentId, activePath)} download>
                            <button className="workspace-op-icon-btn" title="Download">↓</button>
                        </a>
                    )}
                </div>
            </div>

            <div className={`workspace-op-body ${activityOpen ? 'activity-open' : ''} ${treeOpen ? '' : 'tree-closed'}`}>
                <div className="workspace-op-main">
                    <button className={`workspace-op-tree-float ${treeOpen && !activityOpen ? 'active' : ''}`} onClick={() => {
                        setActivityOpen(false);
                        setTreeOpen((open) => !open);
                    }} title={treeOpen && !activityOpen ? 'Hide files' : 'Show files'} aria-label={treeOpen && !activityOpen ? 'Hide files' : 'Show files'}>
                        <span>{treeOpen && !activityOpen ? '›' : '‹'}</span>
                    </button>
                    {renderPreview()}
                </div>
                {activityOpen ? (
                    <aside className="workspace-op-side">
                        <div className="workspace-op-side-title">Version history</div>
                        {!activePath && <div className="workspace-op-side-empty">Open a file to view its history.</div>}
                        {activePath && revisions.length === 0 && <div className="workspace-op-side-empty">No versions recorded yet.</div>}
                        {activePath && revisions.slice(0, 10).map((rev) => (
                            <div className="workspace-op-revision" key={rev.id}>
                                <div>
                                    <strong>{rev.operation}</strong>
                                    <span>{rev.actor_type}</span>
                                </div>
                                <pre>{rev.diff || 'No text diff'}</pre>
                                {rev.after_content != null && <button className="btn btn-secondary" onClick={() => restore(rev.id)}>Restore</button>}
                            </div>
                        ))}
                    </aside>
                ) : treeOpen ? (
                    <aside className="workspace-op-tree">
                        <div className="workspace-op-tree-list">
                            {fileTree.length ? renderFileTreeNodes(fileTree, 0) : <div className="workspace-op-tree-empty">No files yet.</div>}
                        </div>
                    </aside>
                ) : null}
            </div>
        </div>
    );
}
