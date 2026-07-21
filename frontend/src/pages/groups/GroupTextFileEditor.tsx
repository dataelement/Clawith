import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useToast } from '../../components/Toast/ToastProvider';
import type { GroupTextFile } from '../../types/group';

interface GroupTextFileEditorProps {
    queryKey: unknown[];
    note?: string;
    placeholder?: string;
    load: () => Promise<GroupTextFile>;
    save: (content: string, expectedVersionToken: string | null) => Promise<GroupTextFile>;
    onDelete?: () => Promise<void>;
    deleteLabel?: string;
}

/**
 * Shared editor for the group's fixed-path markdown files (announcement, per-agent memory).
 * Writes carry the version token they were loaded with, so a concurrent edit is rejected by the
 * backend rather than silently overwritten.
 */
export default function GroupTextFileEditor({
    queryKey,
    note,
    placeholder,
    load,
    save,
    onDelete,
    deleteLabel,
}: GroupTextFileEditorProps) {
    const { t } = useTranslation();
    const toast = useToast();
    const [draft, setDraft] = useState('');
    const [dirty, setDirty] = useState(false);
    const [busy, setBusy] = useState(false);

    const { data, isLoading, error, refetch } = useQuery({
        queryKey,
        queryFn: load,
        retry: false,
    });

    // A fresh load only replaces the textarea when the user has no unsaved edits in it.
    useEffect(() => {
        if (data && !dirty) setDraft(data.content);
    }, [data, dirty]);

    useEffect(() => {
        setDraft('');
        setDirty(false);
    }, [JSON.stringify(queryKey)]);

    const commit = async () => {
        setBusy(true);
        try {
            await save(draft, data?.version_token ?? null);
            toast.success(t('groups.fileSaved', '已保存'));
            setDirty(false);
            await refetch();
        } catch (err: any) {
            toast.error(err?.message ?? t('groups.fileSaveFailed', '保存失败'));
        } finally {
            setBusy(false);
        }
    };

    const remove = async () => {
        if (!onDelete) return;
        setBusy(true);
        try {
            await onDelete();
            setDraft('');
            setDirty(false);
            await refetch();
            toast.success(t('groups.fileDeleted', '已删除'));
        } catch (err: any) {
            toast.error(err?.message ?? t('groups.fileDeleteFailed', '删除失败'));
        } finally {
            setBusy(false);
        }
    };

    if (error) {
        return (
            <div className="group-empty-hint">
                {(error as any)?.message ?? t('groups.fileLoadFailed', '读取失败')}
            </div>
        );
    }

    return (
        <>
            {note && <div className="group-panel-note">{note}</div>}
            <textarea
                className="group-announcement-input"
                value={draft}
                disabled={isLoading || busy}
                onChange={(event) => {
                    setDraft(event.target.value);
                    setDirty(true);
                }}
                placeholder={placeholder}
            />
            <div className="group-panel-actions">
                <button
                    type="button"
                    className="btn btn-sm"
                    disabled={!dirty || busy}
                    onClick={() => void commit()}
                >
                    {busy ? t('common.loading', '加载中...') : t('common.save', '保存')}
                </button>
                {onDelete && data?.exists && (
                    <button
                        type="button"
                        className="btn btn-sm danger"
                        disabled={busy}
                        onClick={() => void remove()}
                    >
                        {deleteLabel ?? t('common.delete', '删除')}
                    </button>
                )}
            </div>
        </>
    );
}
