import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type {
    SkillFolderUploadApplyInput,
    SkillFolderUploadApplyResult,
    SkillFolderUploadPreview,
} from '../../services/api';
import { buildSkillFolderZip, deriveSkillRootName } from '../../utils/skillFolderZip';

type SkillFolderUploadModalProps = {
    open: boolean;
    onClose: () => void;
    i18nPrefix: string;
    cancelLabelKey?: string;
    previewRequest: (file: File, targetFolder: string) => Promise<SkillFolderUploadPreview>;
    applyRequest: (input: SkillFolderUploadApplyInput) => Promise<SkillFolderUploadApplyResult>;
    onApplied?: (result: SkillFolderUploadApplyResult) => void | Promise<void>;
};

function getRelativeSkillPath(file: File): string {
    const relativePath = file.webkitRelativePath || file.name;
    const segments = relativePath.split('/').filter(Boolean);
    return segments.length > 1 ? segments.slice(1).join('/') : segments[0] || file.name;
}

function PreviewList({ title, paths }: { title: string; paths: string[] }) {
    if (!paths.length) return null;

    return (
        <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>{title}</div>
            <div style={{ maxHeight: '160px', overflowY: 'auto', background: 'var(--bg-secondary)', borderRadius: '8px', padding: '10px 12px' }}>
                {paths.map((path) => (
                    <div key={path} style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5, wordBreak: 'break-all' }}>{path}</div>
                ))}
            </div>
        </div>
    );
}

export default function SkillFolderUploadModal({
    open,
    onClose,
    i18nPrefix,
    cancelLabelKey = 'common.cancel',
    previewRequest,
    applyRequest,
    onApplied,
}: SkillFolderUploadModalProps) {
    const { t } = useTranslation();
    const inputRef = useRef<HTMLInputElement | null>(null);
    const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
    const [sourceFolderName, setSourceFolderName] = useState('');
    const [targetFolder, setTargetFolder] = useState('');
    const [preview, setPreview] = useState<SkillFolderUploadPreview | null>(null);
    const [preparedFile, setPreparedFile] = useState<File | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [applying, setApplying] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const openRef = useRef(open);
    const previewRequestIdRef = useRef(0);
    const applyRequestIdRef = useRef(0);
    const previewStateVersionRef = useRef(0);

    const invalidatePreviewState = (options?: { clearError?: boolean }) => {
        previewStateVersionRef.current += 1;
        previewRequestIdRef.current += 1;
        applyRequestIdRef.current += 1;
        setPreview(null);
        setPreparedFile(null);
        setPreviewing(false);
        setApplying(false);
        if (options?.clearError) setError(null);
    };

    const handleClose = () => {
        invalidatePreviewState({ clearError: true });
        onClose();
    };

    useEffect(() => {
        const input = inputRef.current;
        if (!input) return;
        input.setAttribute('webkitdirectory', '');
        input.setAttribute('directory', '');
    }, []);

    useEffect(() => {
        openRef.current = open;
    }, [open]);

    useEffect(() => {
        if (!open) {
            invalidatePreviewState({ clearError: true });
            setSelectedFiles([]);
            setSourceFolderName('');
            setTargetFolder('');
            setPreviewing(false);
            setApplying(false);
            if (inputRef.current) inputRef.current.value = '';
        }
    }, [open]);

    const selectedPaths = useMemo(() => selectedFiles.map((file) => getRelativeSkillPath(file)), [selectedFiles]);
    const modalT = (key: string, options?: Record<string, unknown>) => t(`${i18nPrefix}.${key}`, options);

    const openPicker = () => inputRef.current?.click();

    const handleFilesSelected = (files: File[]) => {
        if (!files.length) return;
        const derivedRoot = deriveSkillRootName(files);
        setSelectedFiles(files);
        setSourceFolderName(derivedRoot);
        setTargetFolder(derivedRoot);
        invalidatePreviewState({ clearError: true });
    };

    const buildUploadFile = async (folderName: string) => buildSkillFolderZip(
        selectedFiles.map((file) => ({ path: getRelativeSkillPath(file), file })),
        folderName,
    );

    const handlePreview = async () => {
        if (!selectedFiles.length) {
            setError(modalT('validation.pickFolderFirst'));
            return;
        }

        const normalizedTarget = targetFolder.trim();
        if (!normalizedTarget) {
            setError(modalT('validation.targetRequired'));
            return;
        }

        setPreviewing(true);
        setError(null);
        const requestId = ++previewRequestIdRef.current;
        const stateVersion = previewStateVersionRef.current;
        try {
            const uploadFile = await buildUploadFile(normalizedTarget);
            if (
                !openRef.current
                || requestId !== previewRequestIdRef.current
                || stateVersion !== previewStateVersionRef.current
            ) {
                return;
            }
            const nextPreview = await previewRequest(uploadFile, normalizedTarget);
            if (
                !openRef.current
                || requestId !== previewRequestIdRef.current
                || stateVersion !== previewStateVersionRef.current
            ) {
                return;
            }
            setPreparedFile(uploadFile);
            setPreview(nextPreview);
        } catch (err: any) {
            if (
                openRef.current
                && requestId === previewRequestIdRef.current
                && stateVersion === previewStateVersionRef.current
            ) {
                setPreparedFile(null);
                setPreview(null);
                setError(String(err?.message || err));
            }
        } finally {
            if (
                openRef.current
                && requestId === previewRequestIdRef.current
                && stateVersion === previewStateVersionRef.current
            ) {
                setPreviewing(false);
            }
        }
    };

    const handleApply = async () => {
        if (!preview || !preparedFile) {
            setError(modalT('validation.previewRequired'));
            return;
        }

        setApplying(true);
        setError(null);
        const requestId = ++applyRequestIdRef.current;
        const stateVersion = previewStateVersionRef.current;
        const previewSnapshot = preview;
        const preparedFileSnapshot = preparedFile;
        try {
            const result = await applyRequest({
                file: preparedFileSnapshot,
                targetFolder: previewSnapshot.target_folder,
                expectedDigest: previewSnapshot.digest,
                expectedTargetStateDigest: previewSnapshot.target_state_digest,
                replaceConfirmed: true,
            });
            if (
                !openRef.current
                || requestId !== applyRequestIdRef.current
                || stateVersion !== previewStateVersionRef.current
            ) {
                return;
            }
            await onApplied?.(result);
            handleClose();
        } catch (err: any) {
            if (
                openRef.current
                && requestId === applyRequestIdRef.current
                && stateVersion === previewStateVersionRef.current
            ) {
                setError(String(err?.message || err));
            }
        } finally {
            if (
                openRef.current
                && requestId === applyRequestIdRef.current
                && stateVersion === previewStateVersionRef.current
            ) {
                setApplying(false);
            }
        }
    };

    if (!open) return null;

    return (
        <div
            style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            onClick={handleClose}
        >
            <div
                onClick={(event) => event.stopPropagation()}
                style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '760px', width: '92%', maxHeight: '82vh', display: 'flex', flexDirection: 'column', gap: '16px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}
            >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
                    <div>
                        <h3 style={{ margin: 0 }}>{modalT('title')}</h3>
                        <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                            {modalT('description')}
                        </p>
                    </div>
                    <button onClick={handleClose} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)', padding: '4px 8px' }}>✕</button>
                </div>

                <input
                    ref={inputRef}
                    type="file"
                    multiple
                    style={{ display: 'none' }}
                    onChange={(event) => handleFilesSelected(Array.from(event.target.files || []))}
                />

                <div style={{ display: 'grid', gap: '12px' }}>
                    <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                        <button className="btn btn-secondary" onClick={openPicker}>
                            {modalT('pickFolder')}
                        </button>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {selectedFiles.length
                                ? modalT('selectedSummary', { folder: sourceFolderName, count: selectedFiles.length })
                                : modalT('noFolderSelected')}
                        </div>
                    </div>

                    <div>
                        <label style={{ display: 'block', marginBottom: '6px', fontSize: '13px', fontWeight: 600 }}>
                            {modalT('targetFolderLabel')}
                        </label>
                        <input
                            className="input"
                            value={targetFolder}
                            onChange={(event) => {
                                setTargetFolder(event.target.value);
                                invalidatePreviewState();
                            }}
                            placeholder={modalT('targetFolderPlaceholder')}
                            style={{ width: '100%', boxSizing: 'border-box' }}
                        />
                    </div>
                </div>

                {error && (
                    <div style={{ padding: '10px 12px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.12)', color: '#f87171', fontSize: '12px', lineHeight: 1.5 }}>
                        {error}
                    </div>
                )}

                {selectedPaths.length > 0 && !preview && (
                    <div style={{ maxHeight: '180px', overflowY: 'auto', background: 'var(--bg-secondary)', borderRadius: '8px', padding: '10px 12px' }}>
                        {selectedPaths.slice(0, 20).map((path) => (
                            <div key={path} style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5, wordBreak: 'break-all' }}>{path}</div>
                        ))}
                        {selectedPaths.length > 20 && (
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '6px' }}>
                                {modalT('moreFiles', { count: selectedPaths.length - 20 })}
                            </div>
                        )}
                    </div>
                )}

                {preview && (
                    <div style={{ display: 'grid', gap: '12px', overflowY: 'auto' }}>
                        <div style={{ padding: '12px 14px', borderRadius: '8px', background: 'var(--bg-secondary)', display: 'grid', gap: '8px' }}>
                            <div style={{ fontSize: '13px', fontWeight: 600 }}>
                                {preview.mode === 'create'
                                    ? modalT('previewCreate')
                                    : modalT('previewUpdate')}
                            </div>
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                                <span>{modalT('summary.added', { count: preview.added_count })}</span>
                                <span>{modalT('summary.changed', { count: preview.changed_count })}</span>
                                <span>{modalT('summary.deleted', { count: preview.deleted_count })}</span>
                            </div>
                            {(preview.changed_count > 0 || preview.deleted_count > 0) && (
                                <div style={{ fontSize: '12px', color: '#fbbf24', lineHeight: 1.5 }}>
                                    {preview.deleted_count > 0
                                        ? modalT('warnings.deleteReplace')
                                        : modalT('warnings.replace')}
                                </div>
                            )}
                        </div>

                        <div style={{ display: 'grid', gap: '12px', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
                            <PreviewList title={modalT('diff.added')} paths={preview.added_paths} />
                            <PreviewList title={modalT('diff.changed')} paths={preview.changed_paths} />
                            <PreviewList title={modalT('diff.deleted')} paths={preview.deleted_paths} />
                        </div>
                    </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: 'auto' }}>
                    <button className="btn btn-secondary" onClick={handleClose} disabled={previewing || applying}>
                        {t(cancelLabelKey)}
                    </button>
                    <button className="btn btn-secondary" onClick={handlePreview} disabled={previewing || applying}>
                        {previewing ? modalT('previewing') : modalT('preview')}
                    </button>
                    <button className="btn btn-primary" onClick={handleApply} disabled={!preview || previewing || applying}>
                        {applying ? modalT('applying') : modalT('apply')}
                    </button>
                </div>
            </div>
        </div>
    );
}
