import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import FileBrowser, { type FileBrowserApi } from '../../components/FileBrowser';
import { groupApi } from '../../services/groupApi';
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
            write: (path, content, expectedVersionToken) =>
                groupApi.saveWorkspaceFile(groupId, path, content, expectedVersionToken),
            delete: (path, expectedVersionToken) =>
                groupApi.deleteWorkspaceFile(groupId, path, expectedVersionToken),
        });
        return {
            list: async (path: string) => {
                const entries = await groupApi.workspace(groupId, path);
                return entries.map((entry) => {
                    if (!entry.is_dir) versioned.remember(entry.path, entry.version_token);
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
        };
    }, [groupId]);

    return (
        <div className="group-workspace-tab">
            <div className="group-panel-note">
                {t('groups.workspaceNote', '群 workspace 是全群共享的文件区，群内所有会话共用同一份。智能体的产物也会放在这里。')}
            </div>
            <FileBrowser
                api={api}
                features={{
                    upload: false,
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
