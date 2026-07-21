import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import FileBrowser, { type FileBrowserApi } from '../../components/FileBrowser';
import { groupApi } from '../../services/groupApi';
import {
    GROUP_WORKSPACE_UPLOAD_ACCEPT,
    GroupWorkspaceUploadError,
    groupWorkspaceUploadPath,
    readGroupWorkspaceTextUpload,
} from './groupWorkspaceUpload';
import { createVersionedFileAdapter } from './versionedFileAdapter';

/**
 * The group workspace is where agents drop their outputs and members share files. It is one shared
 * space per group — every session in the group reads and writes the same tree.
 */
export default function GroupWorkspaceTab({ groupId }: { groupId: string }) {
    const { t } = useTranslation();

    const api = useMemo<FileBrowserApi>(() => {
        const versioned = createVersionedFileAdapter({
            read: (path) => groupApi.workspaceFile(groupId, path),
            write: (path, content, expectedVersionToken, requireAbsent) =>
                groupApi.saveWorkspaceFile(
                    groupId,
                    path,
                    content,
                    expectedVersionToken,
                    requireAbsent,
                ),
            delete: (path, expectedVersionToken) =>
                groupApi.deleteWorkspaceFile(groupId, path, expectedVersionToken),
        });
        return {
            list: async (path: string) => {
                const entries = await groupApi.workspace(groupId, path);
                return entries.map((entry) => {
                    versioned.remember(entry.path, entry.version_token);
                    return {
                        name: entry.name,
                        path: entry.path,
                        is_dir: entry.is_dir,
                        size: entry.size,
                    };
                });
            },
            read: versioned.read,
            write: versioned.write,
            delete: versioned.delete,
            upload: async (file, currentPath, onProgress) => {
                try {
                    const path = groupWorkspaceUploadPath(currentPath, file.name);
                    onProgress?.(10);
                    const content = await readGroupWorkspaceTextUpload(file);
                    onProgress?.(40);

                    const snapshot = versioned.snapshot(path);

                    const saved = await groupApi.saveWorkspaceFile(
                        groupId,
                        path,
                        content,
                        snapshot.versionToken,
                        !snapshot.known,
                    );
                    versioned.remember(path, saved.version_token);
                    onProgress?.(100);
                    return saved;
                } catch (error) {
                    if (error instanceof GroupWorkspaceUploadError) {
                        const message = error.code === 'invalid_name'
                            ? t('groups.workspaceUploadInvalidName', '文件名不能包含路径分隔符')
                            : error.code === 'unsupported_type'
                                ? t('groups.workspaceUploadTextOnly', '群文件区当前只支持 UTF-8 文本文件')
                                : t('groups.workspaceUploadInvalidUtf8', '文件不是有效的 UTF-8 文本，未上传')
                        throw new Error(message);
                    }
                    throw error;
                }
            },
        };
    }, [groupId, t]);

    return (
        <div className="group-workspace-tab">
            <div className="group-panel-note">
                {t('groups.workspaceNote', '群 workspace 是全群共享的文件区，群内所有会话共用同一份。智能体的产物也会放在这里。')}
            </div>
            <FileBrowser
                api={api}
                uploadAccept={GROUP_WORKSPACE_UPLOAD_ACCEPT}
                features={{
                    upload: true,
                    newFile: true,
                    newFolder: true,
                    edit: true,
                    delete: true,
                    directoryNavigation: true,
                }}
            />
        </div>
    );
}
