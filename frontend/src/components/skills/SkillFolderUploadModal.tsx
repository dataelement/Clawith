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

    useEffect(() => {
        const input = inputRef.current;
        if (!input) return;
        input.setAttribute('webkitdirectory', '');
        input.setAttribute('directory', '');
    }, []);

    useEffect(() => {
        if (!open) {
            setSelectedFiles([]);
            setSourceFolderName('');
            setTargetFolder('');
            setPreview(null);
            setPreparedFile(null);
            setPreviewing(false);
            setApplying(false);
            setError(null);
            if (inputRef.current) inputRef.current.value = '';
        }
    }, [open]);

    const selectedPaths = useMemo(() => selectedFiles.map((file) => getRelativeSkillPath(file)), [selectedFiles]);

    const openPicker = () => inputRef.current?.click();

    const handleFilesSelected = (files: File[]) => {
        if (!files.length) return;
        const derivedRoot = deriveSkillRootName(files);
        setSelectedFiles(files);
        setSourceFolderName(derivedRoot);
        setTargetFolder(derivedRoot);
        setPreview(null);
        setPreparedFile(null);
        setError(null);
    };

    const buildUploadFile = async (folderName: string) => buildSkillFolderZip(
        selectedFiles.map((file) => ({ path: getRelativeSkillPath(file), file })),
        folderName,
    );

    const handlePreview = async () => {
        if (!selectedFiles.length) {
            setError(t('agent.skills.uploadFolderModal.validation.pickFolderFirst'));
            return;
        }

        const normalizedTarget = targetFolder.trim();
        if (!normalizedTarget) {
            setError(t('agent.skills.uploadFolderModal.validation.targetRequired'));
            return;
        }

        setPreviewing(true);
        setError(null);
        try {
            const uploadFile = await buildUploadFile(normalizedTarget);
            const nextPreview = await previewRequest(uploadFile, normalizedTarget);
            setPreparedFile(uploadFile);
            setPreview(nextPreview);
        } catch (err: any) {
            setPreparedFile(null);
            setPreview(null);
            setError(String(err?.message || err));
        } finally {
            setPreviewing(false);
        }
    };

    const handleApply = async () => {
        if (!preview || !preparedFile) {
            setError(t('agent.skills.uploadFolderModal.validation.previewRequired'));
            return;
        }

        setApplying(true);
        setError(null);
        try {
            const result = await applyRequest({
                file: preparedFile,
                targetFolder: preview.target_folder,
                expectedDigest: preview.digest,
                expectedTargetStateDigest: preview.target_state_digest,
                replaceConfirmed: true,
            });
            await onApplied?.(result);
            onClose();
        } catch (err: any) {
            setError(String(err?.message || err));
        } finally {
            setApplying(false);
        }
    };

    if (!open) return null;

    return (
        <div
            style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            onClick={onClose}
        >
            <div
                onClick={(event) => event.stopPropagation()}
                style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '760px', width: '92%', maxHeight: '82vh', display: 'flex', flexDirection: 'column', gap: '16px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}
            >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
                    <div>
                        <h3 style={{ margin: 0 }}>{t('agent.skills.uploadFolderModal.title')}</h3>
                        <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                            {t('agent.skills.uploadFolderModal.description')}
                        </p>
                    </div>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)', padding: '4px 8px' }}>✕</button>
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
                            {t('agent.skills.uploadFolderModal.pickFolder')}
                        </button>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {selectedFiles.length
                                ? t('agent.skills.uploadFolderModal.selectedSummary', { folder: sourceFolderName, count: selectedFiles.length })
                                : t('agent.skills.uploadFolderModal.noFolderSelected')}
                        </div>
                    </div>

                    <div>
                        <label style={{ display: 'block', marginBottom: '6px', fontSize: '13px', fontWeight: 600 }}>
                            {t('agent.skills.uploadFolderModal.targetFolderLabel')}
                        </label>
                        <input
                            className="input"
                            value={targetFolder}
                            onChange={(event) => {
                                setTargetFolder(event.target.value);
                                setPreview(null);
                                setPreparedFile(null);
                            }}
                            placeholder={t('agent.skills.uploadFolderModal.targetFolderPlaceholder')}
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
                                {t('agent.skills.uploadFolderModal.moreFiles', { count: selectedPaths.length - 20 })}
                            </div>
                        )}
                    </div>
                )}

                {preview && (
                    <div style={{ display: 'grid', gap: '12px', overflowY: 'auto' }}>
                        <div style={{ padding: '12px 14px', borderRadius: '8px', background: 'var(--bg-secondary)', display: 'grid', gap: '8px' }}>
                            <div style={{ fontSize: '13px', fontWeight: 600 }}>
                                {preview.mode === 'create'
                                    ? t('agent.skills.uploadFolderModal.previewCreate')
                                    : t('agent.skills.uploadFolderModal.previewUpdate')}
                            </div>
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                                <span>{t('agent.skills.uploadFolderModal.summary.added', { count: preview.added_count })}</span>
                                <span>{t('agent.skills.uploadFolderModal.summary.changed', { count: preview.changed_count })}</span>
                                <span>{t('agent.skills.uploadFolderModal.summary.deleted', { count: preview.deleted_count })}</span>
                            </div>
                            {(preview.changed_count > 0 || preview.deleted_count > 0) && (
                                <div style={{ fontSize: '12px', color: '#fbbf24', lineHeight: 1.5 }}>
                                    {preview.deleted_count > 0
                                        ? t('agent.skills.uploadFolderModal.warnings.deleteReplace')
                                        : t('agent.skills.uploadFolderModal.warnings.replace')}
                                </div>
                            )}
                        </div>

                        <div style={{ display: 'grid', gap: '12px', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
                            <PreviewList title={t('agent.skills.uploadFolderModal.diff.added')} paths={preview.added_paths} />
                            <PreviewList title={t('agent.skills.uploadFolderModal.diff.changed')} paths={preview.changed_paths} />
                            <PreviewList title={t('agent.skills.uploadFolderModal.diff.deleted')} paths={preview.deleted_paths} />
                        </div>
                    </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: 'auto' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={previewing || applying}>
                        {t('agent.skills.cancel')}
                    </button>
                    <button className="btn btn-secondary" onClick={handlePreview} disabled={previewing || applying}>
                        {previewing ? t('agent.skills.uploadFolderModal.previewing') : t('agent.skills.uploadFolderModal.preview')}
                    </button>
                    <button className="btn btn-primary" onClick={handleApply} disabled={!preview || previewing || applying}>
                        {applying ? t('agent.skills.uploadFolderModal.applying') : t('agent.skills.uploadFolderModal.apply')}
                    </button>
                </div>
            </div>
        </div>
    );
}
