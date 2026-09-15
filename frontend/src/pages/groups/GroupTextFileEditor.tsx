import { useState } from "react";
import { useTranslation } from "react-i18next";
import { caughtErrorMessage } from "../../services/apiError";
import { useQuery } from "@tanstack/react-query";
import { useToast } from "../../components/Toast/ToastContext";
import type { GroupTextFile } from "../../types/group";

interface GroupTextFileEditorProps {
  queryKey: unknown[];
  note?: string;
  placeholder?: string;
  load: () => Promise<GroupTextFile>;
  save: (
    content: string,
    expectedVersionToken: string | null,
  ) => Promise<GroupTextFile>;
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
  return (
    <GroupTextFileEditorContent
      key={JSON.stringify(queryKey)}
      queryKey={queryKey}
      note={note}
      placeholder={placeholder}
      load={load}
      save={save}
      onDelete={onDelete}
      deleteLabel={deleteLabel}
    />
  );
}

function GroupTextFileEditorContent({
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
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey,
    queryFn: load,
    retry: false,
  });
  const content = draft ?? data?.content ?? "";
  const dirty = draft !== null;

  const commit = async () => {
    setBusy(true);
    try {
      await save(content, data?.version_token ?? null);
      toast.success(t("groups.fileSaved", "已保存"));
      setDraft(null);
      await refetch();
    } catch (error) {
      toast.error(
        caughtErrorMessage(error) ?? t("groups.fileSaveFailed", "保存失败"),
      );
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!onDelete) return;
    setBusy(true);
    try {
      await onDelete();
      setDraft(null);
      await refetch();
      toast.success(t("groups.fileDeleted", "已删除"));
    } catch (error) {
      toast.error(
        caughtErrorMessage(error) ?? t("groups.fileDeleteFailed", "删除失败"),
      );
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="group-empty-hint">
        {caughtErrorMessage(error) ?? t("groups.fileLoadFailed", "读取失败")}
      </div>
    );
  }

  return (
    <>
      {note && <div className="group-panel-note">{note}</div>}
      <textarea
        className="group-announcement-input"
        value={content}
        disabled={isLoading || busy}
        onChange={(event) => {
          setDraft(event.target.value);
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
          {busy ? t("common.loading", "加载中...") : t("common.save", "保存")}
        </button>
        {onDelete && data?.exists && (
          <button
            type="button"
            className="btn btn-sm danger"
            disabled={busy}
            onClick={() => void remove()}
          >
            {deleteLabel ?? t("common.delete", "删除")}
          </button>
        )}
      </div>
    </>
  );
}
