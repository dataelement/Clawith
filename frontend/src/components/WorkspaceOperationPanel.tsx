import type { MouseEvent as ReactMouseEvent } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import MarkdownRenderer from './MarkdownRenderer';
import { fileApi, uploadFileWithProgress } from '../services/api';

export interface WorkspaceActivity {
    action: 'write' | 'edit' | 'convert' | 'delete';
    path: string;
    tool?: string;
    ok?: boolean;
    pendingApproval?: boolean;
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

interface UploadItem {
    id: string;
    name: string;
    dir: string;
    progress: number;
    status: 'uploading' | 'processing' | 'done' | 'error';
    error?: string;
}

interface Props {
    agentId: string;
    sessionId?: string;
    activePath?: string | null;
    activities: WorkspaceActivity[];
    liveDraft?: WorkspaceLiveDraft | null;
    locked?: boolean;
    onSelectPath: (path: string) => void;
    onToggleLock?: () => void;
    onEditingChange?: (editing: boolean) => void;
    onPathDeleted?: (path: string) => void;
}

const WORKSPACE_ROOT = 'workspace';
const SKILLS_ROOT = 'skills';
const MEMORY_ROOT = 'memory';
const DEFAULT_UPLOAD_DIR = 'workspace/uploads';
type TreeScope = 'workspace' | 'all';
const EDITABLE_EXTS = new Set(['.md', '.markdown', '.csv']);
const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg']);
const PREVIEW_EXTS = new Set(['.md', '.markdown', '.csv', '.html', '.htm', '.pdf', '.xlsx', '.xls', '.docx', '.doc', '.pptx', '.ppt', '.txt', '.log', '.json', ...IMAGE_EXTS]);
const MIN_SAVING_VISIBLE_MS = 650;
const SAVED_VISIBLE_MS = 1600;
const DEFAULT_TREE_WIDTH = 240;
const DEFAULT_HISTORY_WIDTH = 320;
const MIN_SIDE_WIDTH = 220;
const MAX_SIDE_WIDTH = 520;

function extOf(path: string): string {
    const idx = path.lastIndexOf('.');
    return idx >= 0 ? path.slice(idx).toLowerCase() : '';
}

function parseCsv(text: string): string[][] {
    const nonEmpty = text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .slice(0, 10);
    const delimiters = [',', '，', ';', '\t', '|'];
    const delimiter = delimiters
        .map((candidate) => ({
            candidate,
            score: nonEmpty.reduce((total, line) => total + (line.split(candidate).length - 1), 0),
        }))
        .sort((a, b) => b.score - a.score)[0]?.candidate || ',';
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
        } else if (ch === delimiter && !quoted) {
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
    if (!path) return [WORKSPACE_ROOT];
    const parts = path.split('/').filter(Boolean);
    const dirs: string[] = [];
    for (let i = 0; i < parts.length - 1; i += 1) {
        dirs.push(parts.slice(0, i + 1).join('/'));
    }
    if (!dirs.length) dirs.push(WORKSPACE_ROOT);
    return dirs;
}

function parentDir(path?: string | null): string {
    if (!path || !path.startsWith(`${WORKSPACE_ROOT}/`)) return WORKSPACE_ROOT;
    const parts = path.split('/');
    return parts.length > 1 ? parts.slice(0, -1).join('/') : WORKSPACE_ROOT;
}

function directoryOf(path?: string | null): string {
    if (!path) return WORKSPACE_ROOT;
    const parts = path.split('/').filter(Boolean);
    return parts.length > 1 ? parts.slice(0, -1).join('/') : WORKSPACE_ROOT;
}

function isWritableDir(path?: string | null): boolean {
    if (!path) return false;
    return path === WORKSPACE_ROOT
        || path === SKILLS_ROOT
        || path.startsWith(`${WORKSPACE_ROOT}/`)
        || path.startsWith(`${SKILLS_ROOT}/`);
}

function normalizeWritableDir(path?: string | null): string {
    if (isWritableDir(path)) return path as string;
    return DEFAULT_UPLOAD_DIR;
}

function formatRevisionTime(value?: string | null): string {
    if (!value) return '';
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return '';
    const mm = String(dt.getMonth() + 1).padStart(2, '0');
    const dd = String(dt.getDate()).padStart(2, '0');
    const hh = String(dt.getHours()).padStart(2, '0');
    const min = String(dt.getMinutes()).padStart(2, '0');
    return `${mm}-${dd} ${hh}:${min}`;
}

function buildPreviewVersion(content: string): string {
    let hash = 0;
    for (let i = 0; i < content.length; i += 1) {
        hash = (hash * 31 + content.charCodeAt(i)) >>> 0;
    }
    return `${content.length}-${hash}`;
}

function trimTrailingEmpty(row: string[]): string[] {
    const next = [...row];
    while (next.length && !String(next[next.length - 1] || '').trim()) next.pop();
    return next;
}

function buildRevisionDiff(revision: any): string {
    const before = revision.before_content ?? '';
    const after = revision.after_content ?? '';
    if (!before && !after) return 'No preview available for this revision.';
    if (before === after) {
        if (revision.operation === 'restore') return 'Restored this snapshot.';
        if (revision.operation === 'autosave') return 'Autosaved with no textual changes.';
        return 'No textual changes in this revision.';
    }

    const beforeLines = before.split('\n');
    const afterLines = after.split('\n');

    let prefix = 0;
    while (
        prefix < beforeLines.length &&
        prefix < afterLines.length &&
        beforeLines[prefix] === afterLines[prefix]
    ) {
        prefix += 1;
    }

    let suffix = 0;
    while (
        suffix < beforeLines.length - prefix &&
        suffix < afterLines.length - prefix &&
        beforeLines[beforeLines.length - 1 - suffix] === afterLines[afterLines.length - 1 - suffix]
    ) {
        suffix += 1;
    }

    const removed = beforeLines.slice(prefix, beforeLines.length - suffix);
    const added = afterLines.slice(prefix, afterLines.length - suffix);
    const chunks: string[] = [];

    if (removed.length) {
        chunks.push(...removed.map((line: string) => `- ${line}`));
    }
    if (added.length) {
        chunks.push(...added.map((line: string) => `+ ${line}`));
    }

    if (!chunks.length) {
        chunks.push(`Before:\n${before || '(empty)'}`, `After:\n${after || '(empty)'}`);
    }

    return chunks.join('\n');
}

function HtmlPreviewFrame({
    content,
    title,
    src,
    suspendAutoFit = false,
}: {
    content: string;
    title: string;
    src?: string;
    suspendAutoFit?: boolean;
}) {
    const viewportRef = useRef<HTMLDivElement>(null);
    const frameRef = useRef<HTMLIFrameElement>(null);
    const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [renderContent, setRenderContent] = useState(content);
    const [frameHeight, setFrameHeight] = useState(720);
    const observersRef = useRef<{ resize?: ResizeObserver; mutation?: MutationObserver } | null>(null);
    const fitRafRef = useRef<number | null>(null);

    const fitFrame = () => {
        if (suspendAutoFit) return;
        const frame = frameRef.current;
        const doc = frame?.contentDocument;
        if (!frame || !doc?.body) return;

        const body = doc.body;
        const root = doc.documentElement;
        body.style.margin = body.style.margin || '0';
        root.style.margin = root.style.margin || '0';

        const contentHeight = Math.max(root.scrollHeight, body.scrollHeight, body.offsetHeight, 480);
        setFrameHeight(contentHeight);
    };

    const requestFitFrame = () => {
        if (suspendAutoFit) return;
        if (fitRafRef.current != null) cancelAnimationFrame(fitRafRef.current);
        fitRafRef.current = requestAnimationFrame(() => {
            fitRafRef.current = null;
            fitFrame();
        });
    };

    const bindFrameObservers = () => {
        const frame = frameRef.current;
        const doc = frame?.contentDocument;
        const body = doc?.body;
        if (!doc || !body) return;

        observersRef.current?.resize?.disconnect();
        observersRef.current?.mutation?.disconnect();

        if (typeof ResizeObserver !== 'undefined') {
            const resize = new ResizeObserver(() => requestFitFrame());
            resize.observe(body);
            resize.observe(doc.documentElement);
            observersRef.current = { ...(observersRef.current || {}), resize };
        }

        if (typeof MutationObserver !== 'undefined') {
            const mutation = new MutationObserver(() => {
                requestFitFrame();
            });
            mutation.observe(body, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
            });
            observersRef.current = { ...(observersRef.current || {}), mutation };
        }
    };

    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport || typeof ResizeObserver === 'undefined') return;
        const observer = new ResizeObserver(() => {
            requestFitFrame();
        });
        observer.observe(viewport);
        return () => observer.disconnect();
    }, [suspendAutoFit]);

    useEffect(() => {
        if (src) return;
        if (!content) {
            setRenderContent(content);
            return;
        }
        if (!renderContent) {
            setRenderContent(content);
            return;
        }
        if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = setTimeout(() => {
            setRenderContent(content);
            debounceTimerRef.current = null;
        }, 180);
        return () => {
            if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
        };
    }, [content, renderContent]);

    useEffect(() => () => {
        observersRef.current?.resize?.disconnect();
        observersRef.current?.mutation?.disconnect();
        if (fitRafRef.current != null) cancelAnimationFrame(fitRafRef.current);
    }, []);

    useEffect(() => {
        if (!suspendAutoFit) {
            requestFitFrame();
        }
    }, [suspendAutoFit, renderContent, src]);

    return (
        <div className="workspace-op-html-fit" ref={viewportRef}>
            <iframe
                ref={frameRef}
                sandbox="allow-same-origin allow-scripts allow-forms allow-modals allow-popups allow-downloads allow-pointer-lock allow-top-navigation-by-user-activation"
                src={src}
                srcDoc={src ? undefined : renderContent}
                title={title}
                onLoad={() => {
                    requestAnimationFrame(() => {
                        fitFrame();
                        bindFrameObservers();
                        requestFitFrame();
                    });
                }}
                style={{
                    width: '100%',
                    minHeight: '480px',
                    height: `${frameHeight}px`,
                }}
            />
        </div>
    );
}

export default function WorkspaceOperationPanel({
    agentId,
    sessionId,
    activePath,
    activities,
    liveDraft,
    locked = false,
    onSelectPath,
    onToggleLock,
    onEditingChange,
    onPathDeleted,
}: Props) {
    const [preview, setPreview] = useState<any>(null);
    const [content, setContent] = useState('');
    const [draft, setDraft] = useState('');
    const [previewState, setPreviewState] = useState<'idle' | 'loading' | 'ready' | 'deleted'>('idle');
    const [editing, setEditing] = useState(false);
    const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
    const [revisions, setRevisions] = useState<any[]>([]);
    const [fileTree, setFileTree] = useState<WorkspaceFileNode[]>([]);
    const [activityOpen, setActivityOpen] = useState(false);
    const [treeOpen, setTreeOpen] = useState(true);
    const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => new Set());
    const [treeScope, setTreeScope] = useState<TreeScope>('workspace');
    const [pendingSwitchPath, setPendingSwitchPath] = useState<string | null>(null);
    const [sideWidth, setSideWidth] = useState(DEFAULT_TREE_WIDTH);
    const [selectedDirPath, setSelectedDirPath] = useState(WORKSPACE_ROOT);
    const [uploadItems, setUploadItems] = useState<UploadItem[]>([]);
    const [isSideResizing, setIsSideResizing] = useState(false);
    const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const saveStateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const lockTimer = useRef<ReturnType<typeof setInterval> | null>(null);
    const prevActivePathRef = useRef<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

    const ext = activePath ? extOf(activePath) : '';
    const canEdit = !!activePath && EDITABLE_EXTS.has(ext);
    const isHtml = ext === '.html' || ext === '.htm';
    const isImage = IMAGE_EXTS.has(ext);
    const activityKey = activities.map((item) => `${item.action}:${item.path}`).join('|');
    const treeTargetDir = normalizeWritableDir(selectedDirPath || directoryOf(activePath));
    const panelSideWidth = activityOpen ? Math.max(sideWidth, DEFAULT_HISTORY_WIDTH) : sideWidth;
    const draftMatchesActiveFile = !!(liveDraft?.path && activePath && liveDraft.path === activePath);
    const shouldRenderLiveDraft = !!liveDraft && (!activePath || !liveDraft.path || draftMatchesActiveFile);

    const load = async () => {
        if (!activePath) {
            setPreviewState('idle');
            return;
        }
        setPreviewState('loading');
        try {
            const data = await fileApi.preview(agentId, activePath);
            setPreview(data);
            const text = data.content || '';
            setContent(text);
            setDraft(text);
            setRevisions(await fileApi.revisions(agentId, activePath).catch(() => []));
            setPreviewState('ready');
        } catch (err: any) {
            setPreview(null);
            setContent('');
            setDraft('');
            setRevisions([]);
            setPreviewState(err?.status === 404 ? 'deleted' : 'idle');
        }
    };

    const loadFileTree = async () => {
        const loadDir = async (path: string, depth: number): Promise<WorkspaceFileNode[]> => {
            if (depth > 4) return [];
        const items = await fileApi.list(agentId, path).catch(() => []);
        return Promise.all(items.map(async (item: WorkspaceFileNode) => {
            if (!item.is_dir) return item;
            return { ...item, children: await loadDir(item.path, depth + 1) };
        }));
        };
        const roots = await loadDir(treeScope === 'workspace' ? WORKSPACE_ROOT : '', 0);
        setFileTree(roots);
    };

    useEffect(() => {
        if (activePath !== prevActivePathRef.current) {
            prevActivePathRef.current = activePath ?? null;
            setEditing(false);
            onEditingChange?.(false);
        }
        if (!activePath) {
            setPreview(null);
            setContent('');
            setDraft('');
            setRevisions([]);
            setPreviewState('idle');
            return;
        }
        if (liveDraft && (!activePath || !liveDraft.path || liveDraft.path === activePath)) {
            setPreview(null);
            setContent(liveDraft.content || '');
            setDraft(liveDraft.content || '');
            setPreviewState('ready');
            return;
        }
        void load();
    }, [agentId, activePath, liveDraft?.id, liveDraft?.path, onEditingChange]);

    useEffect(() => {
        if (!activePath) return;
        const latestActivity = activities.find((item) => item.path === activePath);
        if (latestActivity?.action !== 'delete' || latestActivity.ok === false || latestActivity.pendingApproval) return;
        setEditing(false);
        onEditingChange?.(false);
        setPreview(null);
        setContent('');
        setDraft('');
        setRevisions([]);
        setPreviewState('deleted');
    }, [activities, activePath, onEditingChange]);

    useEffect(() => {
        if (!activePath) return undefined;
        const latestActivity = activities.find((item) => item.path === activePath);
        if (latestActivity?.action !== 'delete' || !latestActivity.pendingApproval) return undefined;

        let cancelled = false;
        const pollForDeletion = async () => {
            try {
                await fileApi.preview(agentId, activePath);
            } catch (err: any) {
                if (cancelled || err?.status !== 404) return;
                setEditing(false);
                onEditingChange?.(false);
                setPreview(null);
                setContent('');
                setDraft('');
                setRevisions([]);
                setPreviewState('deleted');
                onPathDeleted?.(activePath);
                void loadFileTree();
            }
        };

        void pollForDeletion();
        const timer = window.setInterval(() => {
            void pollForDeletion();
        }, 4000);

        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [activities, activePath, agentId, onEditingChange, onPathDeleted]);

    useEffect(() => {
        loadFileTree();
    }, [agentId, activityKey, liveDraft?.path, treeScope]);

    useEffect(() => {
        if (!activePath || treeScope !== 'workspace') return;
        if (activePath === WORKSPACE_ROOT || activePath.startsWith(`${WORKSPACE_ROOT}/`)) return;
        setTreeScope('all');
    }, [activePath, treeScope]);

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
        if (activePath) {
            setSelectedDirPath(directoryOf(activePath));
        }
    }, [activePath]);

    useEffect(() => {
        setSideWidth((prev) => {
            const base = activityOpen ? DEFAULT_HISTORY_WIDTH : DEFAULT_TREE_WIDTH;
            if (prev < MIN_SIDE_WIDTH || prev > MAX_SIDE_WIDTH) return base;
            return activityOpen ? Math.max(prev, DEFAULT_HISTORY_WIDTH) : prev;
        });
    }, [activityOpen]);

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
    const htmlPreviewSrc = useMemo(() => {
        if (!activePath || !isHtml || editing || shouldRenderLiveDraft) return '';
        const base = fileApi.downloadUrl(agentId, activePath, { inline: true });
        const version = preview?.content_hash || buildPreviewVersion(content || '');
        return `${base}&v=${encodeURIComponent(version)}`;
    }, [activePath, agentId, isHtml, editing, preview?.content_hash, content, shouldRenderLiveDraft]);

    const csvRows = useMemo(() => {
        if (previewType === 'csv') return parseCsv(editing ? draft : content).slice(0, 200).map(trimTrailingEmpty).filter((row) => row.length);
        return [];
    }, [previewType, content, draft, editing]);

    const xlsxRows = previewType === 'xlsx' ? (preview.sheets?.[0]?.rows || []).map(trimTrailingEmpty).filter((row: string[]) => row.length) : [];

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

    const discardEditing = async () => {
        if (saveTimer.current) clearTimeout(saveTimer.current);
        setDraft(content);
        setEditing(false);
        onEditingChange?.(false);
        if (activePath) {
            await fileApi.unlock(agentId, activePath).catch(() => {});
        }
    };

    const switchToPath = (path: string) => {
        if (path === activePath) return;
        if (!editing) {
            onSelectPath(path);
            return;
        }
        setPendingSwitchPath(path);
    };

    const saveAndSwitch = async () => {
        if (!pendingSwitchPath) return;
        const nextPath = pendingSwitchPath;
        setPendingSwitchPath(null);
        await finishEditing();
        onSelectPath(nextPath);
    };

    const discardAndSwitch = async () => {
        if (!pendingSwitchPath) return;
        const nextPath = pendingSwitchPath;
        setPendingSwitchPath(null);
        await discardEditing();
        onSelectPath(nextPath);
    };

    const restore = async (revisionId: string) => {
        if (!activePath) return;
        await fileApi.restoreRevision(agentId, revisionId);
        await load();
    };

    const handleUploadClick = () => {
        fileInputRef.current?.click();
    };

    const switchTreeScope = (scope: TreeScope) => {
        setTreeScope(scope);
        if (scope === 'workspace') {
            setSelectedDirPath(WORKSPACE_ROOT);
        }
    };

    const handleUploadFiles = async (files: FileList | null) => {
        if (!files?.length) return;
        const selectedFiles = Array.from(files);
        for (const file of selectedFiles) {
            const itemId = `${treeTargetDir}:${file.name}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
            setExpandedDirs((prev) => {
                const next = new Set(prev);
                parentDirs(treeTargetDir).forEach((dir) => next.add(dir));
                next.add(treeTargetDir);
                return next;
            });
            setUploadItems((prev) => [...prev, {
                id: itemId,
                name: file.name,
                dir: treeTargetDir,
                progress: 0,
                status: 'uploading',
            }]);
            try {
                const { promise } = uploadFileWithProgress(
                    `/agents/${agentId}/files/upload?path=${encodeURIComponent(treeTargetDir)}`,
                    file,
                    (pct) => {
                        setUploadItems((prev) => prev.map((item) => item.id === itemId
                            ? {
                                ...item,
                                status: pct > 100 ? 'processing' : 'uploading',
                                progress: pct > 100 ? 100 : pct,
                            }
                            : item));
                    },
                );
                await promise;
                setUploadItems((prev) => prev.map((item) => item.id === itemId
                    ? { ...item, status: 'done', progress: 100 }
                    : item));
                await loadFileTree();
                window.setTimeout(() => {
                    setUploadItems((prev) => prev.filter((item) => item.id !== itemId));
                }, 900);
            } catch (err: any) {
                setUploadItems((prev) => prev.map((item) => item.id === itemId
                    ? { ...item, status: 'error', error: err?.message || 'Upload failed' }
                    : item));
            }
        }
    };

    const handleCreateFolder = async () => {
        const name = window.prompt('Folder name');
        if (!name) return;
        const trimmed = name.trim().replace(/^\/+|\/+$/g, '');
        if (!trimmed) return;
        const folderPath = `${treeTargetDir}/${trimmed}`;
        await fileApi.write(agentId, `${folderPath}/.gitkeep`, '');
        setSelectedDirPath(folderPath);
        setExpandedDirs((prev) => {
            const next = new Set(prev);
            parentDirs(folderPath).forEach((dir) => next.add(dir));
            next.add(folderPath);
            next.add(treeTargetDir);
            return next;
        });
        await loadFileTree();
    };

    const deleteTreePath = async (path: string, label: string, selected?: boolean) => {
        if (!confirm(`Are you sure you want to delete ${label}?`)) return;
        try {
            await fileApi.delete(agentId, path);
            if (selected) {
                setEditing(false);
                onEditingChange?.(false);
                setPreview(null);
                setContent('');
                setDraft('');
                setRevisions([]);
                setPreviewState('deleted');
            }
            if (selectedDirPath === path || selectedDirPath.startsWith(`${path}/`)) {
                setSelectedDirPath(WORKSPACE_ROOT);
            }
            onPathDeleted?.(path);
            await loadFileTree();
        } catch (err: any) {
            alert(`Failed to delete: ${err.message}`);
        }
    };

    const startResize = (event: ReactMouseEvent<HTMLDivElement>) => {
        event.preventDefault();
        setIsSideResizing(true);
        resizeRef.current = { startX: event.clientX, startWidth: panelSideWidth };
        const onMove = (moveEvent: MouseEvent) => {
            const next = resizeRef.current
                ? resizeRef.current.startWidth - (moveEvent.clientX - resizeRef.current.startX)
                : panelSideWidth;
            setSideWidth(Math.max(MIN_SIDE_WIDTH, Math.min(MAX_SIDE_WIDTH, next)));
        };
        const onUp = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            resizeRef.current = null;
            setIsSideResizing(false);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
    };

    const renderPreview = () => {
        if (shouldRenderLiveDraft) {
            const draftExt = liveDraft.path ? extOf(liveDraft.path) : ext;
            const draftContent = liveDraft.content || '';
            if (draftExt === '.html' || draftExt === '.htm') {
                return (
                    <div className="workspace-op-live">
                        <div className="workspace-op-live-banner">{liveDraft.status === 'drafting' ? 'Drafting HTML...' : 'Writing HTML...'}</div>
                        {draftContent ? (
                            <HtmlPreviewFrame content={draftContent} title={fileName(liveDraft.path || 'draft.html')} suspendAutoFit={isSideResizing} />
                        ) : (
                            <div className="workspace-op-empty">Preparing file content...</div>
                        )}
                    </div>
                );
            }
            if (draftExt === '.csv') {
                const rows = parseCsv(draftContent).slice(0, 200);
                const [header, ...bodyRows] = rows;
                return (
                    <div className="workspace-op-live">
                        <div className="workspace-op-live-banner">{liveDraft.status === 'drafting' ? 'Drafting CSV...' : 'Writing CSV...'}</div>
                        {rows.length ? (
                            <div className="workspace-op-table-wrap">
                                <table className="workspace-op-table">
                                    {!!header?.length && (
                                        <thead>
                                            <tr>{header.map((cell, j) => <th key={j}>{cell}</th>)}</tr>
                                        </thead>
                                    )}
                                    <tbody>
                                        {bodyRows.map((row, i) => (
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
        if (previewState === 'loading') {
            return <div className="workspace-op-empty">Loading file preview...</div>;
        }
        if (previewState === 'deleted') {
            return (
                <div className="workspace-op-empty workspace-op-deleted">
                    <strong className="workspace-op-deleted-title">This file was deleted.</strong>
                    <span className="workspace-op-deleted-path">{activePath}</span>
                </div>
            );
        }
        if (!preview) {
            return <div className="workspace-op-empty">Preview is not available for this file.</div>;
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
        if (previewType === 'text') {
            return <pre className="workspace-op-text-preview">{preview.content || preview.text || ''}</pre>;
        }
        if (previewType === 'csv') {
            const rows = csvRows;
            const maxCols = rows.reduce((max, row) => Math.max(max, row.length), 0);
            const [header, ...bodyRows] = rows;
            return (
                <div className="workspace-op-table-wrap">
                    <table className="workspace-op-table">
                        {!!header?.length && (
                            <thead>
                                <tr>
                                    {Array.from({ length: maxCols }).map((_, j) => <th key={j}>{header[j] || ''}</th>)}
                                </tr>
                            </thead>
                        )}
                        <tbody>
                            {bodyRows.map((row, i) => (
                                <tr key={i}>
                                    {Array.from({ length: maxCols }).map((_, j) => <td key={j}>{row[j] || ''}</td>)}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            );
        }
        if (previewType === 'xlsx') {
            const rows = xlsxRows;
            const maxCols = rows.reduce((max: number, row: string[]) => Math.max(max, row.length), 0);
            const [header, ...bodyRows] = rows;
            return (
                <div className="workspace-op-table-wrap">
                    <table className="workspace-op-table">
                        {!!header?.length && (
                            <thead>
                                <tr>
                                    {Array.from({ length: maxCols }).map((_, j) => <th key={j}>{header[j] || ''}</th>)}
                                </tr>
                            </thead>
                        )}
                        <tbody>
                            {bodyRows.map((row: string[], i: number) => (
                                <tr key={i}>
                                    {Array.from({ length: maxCols }).map((_, j) => <td key={j}>{row[j] || ''}</td>)}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            );
        }
        if (isHtml) {
            if (isSideResizing) {
                return <div className="workspace-op-empty workspace-op-preview-paused">Release to refresh HTML preview.</div>;
            }
            return <HtmlPreviewFrame content={content || ''} title={fileName(activePath)} src={htmlPreviewSrc || undefined} suspendAutoFit={isSideResizing} />;
        }
        if (isImage) {
            return (
                <div className="workspace-op-image-preview">
                    <img
                        src={fileApi.downloadUrl(agentId, activePath, { inline: true })}
                        alt={fileName(activePath)}
                        className="workspace-op-image"
                    />
                </div>
            );
        }
        if (previewType === 'pdf') {
            if (isSideResizing) {
                return <div className="workspace-op-empty workspace-op-preview-paused">Release to refresh PDF preview.</div>;
            }
            return <iframe className="workspace-op-pdf" src={fileApi.downloadUrl(agentId, activePath, { inline: true })} title={fileName(activePath)} />;
        }
        if (previewType === 'docx') {
            return <pre className="workspace-op-text-preview">{preview.content || preview.text}</pre>;
        }
        if (previewType === 'pptx') {
            if (!(preview.slides || []).length) {
                return <pre className="workspace-op-text-preview">{preview.content || preview.text || ''}</pre>;
            }
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

    const renderUploadRows = (dirPath: string, depth: number) => uploadItems
        .filter((item) => item.dir === dirPath)
        .map((item) => (
            <div
                key={item.id}
                                className={`workspace-op-tree-upload ${item.status}`}
                                style={{ paddingLeft: `${18 + depth * 12}px` }}
                                title={item.error || `${item.progress}%`}
                            >
                <div className="workspace-op-tree-upload-main">
                    <span className="workspace-op-tree-upload-name">{item.name}</span>
                    <span className="workspace-op-tree-upload-status">
                        {item.status === 'error'
                            ? 'Failed'
                            : item.status === 'done'
                                ? 'Done'
                                : item.status === 'processing'
                                    ? 'Processing…'
                                    : `${item.progress}%`}
                    </span>
                </div>
                {item.status !== 'error' && (
                    <div className="workspace-op-tree-upload-bar">
                        <span style={{ width: `${Math.max(6, item.progress)}%` }} />
                    </div>
                )}
                {item.status === 'error' && <div className="workspace-op-tree-upload-error">{item.error}</div>}
            </div>
        ));

    const renderFileTreeNodes = (nodes: WorkspaceFileNode[], depth = 0) => nodes.map((node) => {
        const selected = node.path === activePath;
        if (node.is_dir) {
            const expanded = expandedDirs.has(node.path);
            const dirSelected = selectedDirPath === node.path;
            return (
                <div key={node.path || node.name}>
                    <div className={`workspace-op-tree-dir ${dirSelected ? 'active' : ''}`} style={{ paddingLeft: `${6 + depth * 12}px` }}>
                        <button
                            className="workspace-op-tree-dir-main"
                            onClick={() => {
                                setSelectedDirPath(node.path);
                                setExpandedDirs((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(node.path)) next.delete(node.path);
                                    else next.add(node.path);
                                    return next;
                                });
                            }}
                        >
                            <span className="workspace-op-tree-chevron">{expanded ? '▾' : '▸'}</span>
                            <span>{node.name}</span>
                        </button>
                        {node.path !== WORKSPACE_ROOT && node.path !== SKILLS_ROOT && node.path !== MEMORY_ROOT && (
                            <button
                                className="workspace-op-tree-file-delete"
                                title="Delete folder"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    void deleteTreePath(node.path, node.name);
                                }}
                            >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2M10 11v6M14 11v6"/>
                                </svg>
                            </button>
                        )}
                    </div>
                    {expanded && (
                        <>
                            {isWritableDir(node.path) && renderUploadRows(node.path, depth + 1)}
                            {node.children && renderFileTreeNodes(node.children, depth + 1)}
                        </>
                    )}
                </div>
            );
        }
        return (
            <div
                key={node.path}
                className={`workspace-op-tree-file ${selected ? 'active' : ''}`}
                style={{ paddingLeft: `${18 + depth * 12}px` }}
                onClick={() => switchToPath(node.path)}
                title={node.path}
            >
                <div className="workspace-op-tree-file-name">{node.name}</div>
                {!editing && (
                    <button
                        className="workspace-op-tree-file-delete"
                        title="Delete file"
                        onClick={async (e) => {
                            e.stopPropagation();
                            void deleteTreePath(node.path, node.name, selected);
                        }}
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2M10 11v6M14 11v6"/>
                        </svg>
                    </button>
                )}
            </div>
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
                    {activePath && (
                        <button
                            className={`workspace-op-icon-btn ${locked ? 'active' : ''}`}
                            onClick={onToggleLock}
                            title={locked ? 'Unlock current file' : 'Lock current file'}
                            aria-label={locked ? 'Unlock current file' : 'Lock current file'}
                        >
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                <path d="M4 9V6.5A2.5 2.5 0 016.5 4H9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                                <path d="M15 4h2.5A2.5 2.5 0 0120 6.5V9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                                <path d="M20 15v2.5a2.5 2.5 0 01-2.5 2.5H15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                                <path d="M9 20H6.5A2.5 2.5 0 014 17.5V15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                                <circle cx="12" cy="12" r="2.6" stroke="currentColor" strokeWidth="1.8" />
                            </svg>
                        </button>
                    )}
                    {activePath && canEdit && !editing && <button className="workspace-op-icon-btn" onClick={() => setEditing(true)} title="Edit">✎</button>}
                    {editing && <button className="workspace-op-icon-btn active" onClick={finishEditing} title="Done">✓</button>}
                    {activePath && (
                        <a href={fileApi.downloadUrl(agentId, activePath)} download>
                            <button className="workspace-op-icon-btn" title="Download" aria-label="Download">
                                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                    <path d="M12 3v10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                                    <path d="M8 10l4 4 4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                                    <path d="M5 17v2h14v-2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                                </svg>
                            </button>
                        </a>
                    )}
                </div>
            </div>

            <div
                className={`workspace-op-body ${activityOpen ? 'activity-open' : ''} ${treeOpen ? '' : 'tree-closed'}`}
                style={treeOpen || activityOpen ? {
                    gridTemplateColumns: `minmax(0, 1fr) ${panelSideWidth}px`,
                    ['--workspace-side-width' as any]: `${panelSideWidth}px`,
                } : undefined}
            >
                <button className={`workspace-op-tree-edge-toggle ${treeOpen && !activityOpen ? 'active' : ''}`} onClick={() => {
                    setActivityOpen(false);
                    setTreeOpen((open) => !open);
                }} title={treeOpen && !activityOpen ? 'Hide files' : 'Show files'} aria-label={treeOpen && !activityOpen ? 'Hide files' : 'Show files'}>
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                        <rect x="4" y="5" width="16" height="14" rx="2" stroke="currentColor" strokeWidth="1.9" />
                        <path d="M14 5v14" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
                        <path
                            d={treeOpen && !activityOpen ? 'M10 9l-3 3 3 3' : 'M8 9l3 3-3 3'}
                            stroke="currentColor"
                            strokeWidth="1.9"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                        />
                    </svg>
                </button>
                <div className="workspace-op-main">
                    {renderPreview()}
                </div>
                {(treeOpen || activityOpen) && <div className="workspace-op-side-resize" onMouseDown={startResize} />}
                {activityOpen ? (
                    <aside className="workspace-op-side">
                        <div className="workspace-op-side-title">Version history</div>
                        <div className="workspace-op-side-list">
                            {!activePath && <div className="workspace-op-side-empty">Open a file to view its history.</div>}
                            {activePath && revisions.length === 0 && <div className="workspace-op-side-empty">No versions recorded yet.</div>}
                    {activePath && revisions.map((rev) => {
                        const diffText = buildRevisionDiff(rev);
                        const isNote = diffText.startsWith('No ') || diffText.startsWith('Restored') || diffText.startsWith('Autosaved');
                        return (
                            <div className="workspace-op-revision" key={rev.id}>
                                <div className="workspace-op-revision-head">
                                    <div className="workspace-op-revision-meta">
                                        <strong>{rev.operation}</strong>
                                        <span>{rev.actor_type}</span>
                                    </div>
                                    <time className="workspace-op-revision-time" dateTime={rev.created_at || undefined}>
                                        {formatRevisionTime(rev.created_at)}
                                    </time>
                                </div>
                                <pre className={isNote ? 'workspace-op-revision-note' : ''}>{diffText}</pre>
                                {rev.after_content != null && <button className="btn btn-secondary" onClick={() => restore(rev.id)}>Restore</button>}
                            </div>
                        );
                    })}
                        </div>
                    </aside>
                ) : treeOpen ? (
                    <aside className="workspace-op-tree">
                        <div className="workspace-op-side-title">
                            <div className="workspace-op-tree-tools workspace-op-tree-tools-full">
                                <div className="workspace-op-tree-scope" role="tablist" aria-label="File tree scope">
                                    <button
                                        className={treeScope === 'workspace' ? 'active' : ''}
                                        type="button"
                                        role="tab"
                                        aria-selected={treeScope === 'workspace'}
                                        onClick={() => switchTreeScope('workspace')}
                                    >
                                        Workspace
                                    </button>
                                    <button
                                        className={treeScope === 'all' ? 'active' : ''}
                                        type="button"
                                        role="tab"
                                        aria-selected={treeScope === 'all'}
                                        onClick={() => switchTreeScope('all')}
                                    >
                                        All
                                    </button>
                                </div>
                                <div className="workspace-op-tree-actions">
                                    <button
                                        className="workspace-op-mini-btn workspace-op-mini-btn-icon"
                                        type="button"
                                        onClick={handleUploadClick}
                                        title={`Upload into ${treeTargetDir}`}
                                        aria-label={`Upload into ${treeTargetDir}`}
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                            <path d="M12 16V5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
                                            <path d="M8 9l4-4 4 4" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
                                            <path d="M5 19h14" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
                                        </svg>
                                    </button>
                                    <button
                                        className="workspace-op-mini-btn workspace-op-mini-btn-icon"
                                        type="button"
                                        onClick={handleCreateFolder}
                                        title={`Create folder in ${treeTargetDir}`}
                                        aria-label={`Create folder in ${treeTargetDir}`}
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                            <path d="M4 8.5A2.5 2.5 0 016.5 6H10l1.4 1.6H17.5A2.5 2.5 0 0120 10.1v6.4A2.5 2.5 0 0117.5 19h-11A2.5 2.5 0 014 16.5v-8Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                                            <path d="M12 10.5v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                                            <path d="M9.5 13h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                                        </svg>
                                    </button>
                                </div>
                            </div>
                        </div>
                        <div className="workspace-op-tree-list">
                            {treeScope === 'workspace' && renderUploadRows(WORKSPACE_ROOT, -1)}
                            {fileTree.length ? renderFileTreeNodes(fileTree, 0) : <div className="workspace-op-tree-empty">No files yet.</div>}
                        </div>
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            style={{ display: 'none' }}
                            onChange={async (e) => {
                                await handleUploadFiles(e.target.files);
                                e.target.value = '';
                            }}
                        />
                    </aside>
                ) : null}
            </div>
            {pendingSwitchPath && (
                <div className="workspace-op-modal-overlay" onClick={() => setPendingSwitchPath(null)}>
                    <div className="workspace-op-modal" onClick={(e) => e.stopPropagation()}>
                        <div className="workspace-op-modal-title">Switch files?</div>
                        <div className="workspace-op-modal-text">
                            You are editing <strong>{activePath ? fileName(activePath) : 'the current file'}</strong>.
                            {' '}Choose how to handle your changes before opening <strong>{fileName(pendingSwitchPath)}</strong>.
                        </div>
                        <div className="workspace-op-modal-actions">
                            <button className="btn btn-secondary" onClick={() => setPendingSwitchPath(null)}>Stay Here</button>
                            <button className="btn btn-secondary" onClick={discardAndSwitch}>Discard & Switch</button>
                            <button className="btn btn-primary" onClick={saveAndSwitch}>Save & Switch</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
