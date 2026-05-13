import FileBrowser from '../../../components/FileBrowser';
import type { FileBrowserApi } from '../../../components/FileBrowser';
import { enterpriseApi } from '../../../services/api';

export default function EnterpriseKBBrowser({ onRefresh }: { onRefresh: () => void; refreshKey: number }) {
    const kbAdapter: FileBrowserApi = {
        list: (path) => enterpriseApi.kbFiles(path),
        read: (path) => enterpriseApi.kbRead(path),
        write: (path, content) => enterpriseApi.kbWrite(path, content),
        delete: (path) => enterpriseApi.kbDelete(path),
        upload: (file, path) => enterpriseApi.kbUpload(file, path),
    };

    return (
        <FileBrowser
            api={kbAdapter}
            features={{ upload: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }}
            onRefresh={onRefresh}
        />
    );
}
