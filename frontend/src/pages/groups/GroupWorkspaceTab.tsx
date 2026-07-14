import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import FileBrowser, { type FileBrowserApi } from '../../components/FileBrowser';
import { groupApi } from '../../services/groupApi';

/**
 * The group workspace is where agents drop their outputs and members share files. It is one shared
 * space per group — every session in the group reads and writes the same tree.
 */
export default function GroupWorkspaceTab({ groupId }: { groupId: string }) {
    const { t } = useTranslation();

    const api = useMemo<FileBrowserApi>(() => ({
        list: async (path: string) => {
            const entries = await groupApi.workspace(groupId, path);
            return entries.map((entry) => ({
                name: entry.name,
                path: entry.path,
                is_dir: entry.is_dir,
                size: entry.size,
            }));
        },
        read: async (path: string) => {
            const file = await groupApi.workspaceFile(groupId, path);
            return { content: file.content };
        },
        write: async (path: string, content: string) => {
            // FileBrowser hands us no version token, so read the current one first: writing with it
            // makes the backend reject a save that would clobber someone else's concurrent edit.
            let expected: string | null = null;
            try {
                const current = await groupApi.workspaceFile(groupId, path);
                expected = current.exists ? current.version_token : null;
            } catch {
                expected = null; // A file that does not exist yet is created unconditionally.
            }
            return groupApi.saveWorkspaceFile(groupId, path, content, expected);
        },
        delete: (path: string) => groupApi.deleteWorkspaceFile(groupId, path),
    }), [groupId]);

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
