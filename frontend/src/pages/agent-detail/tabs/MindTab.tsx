import { useTranslation } from 'react-i18next';
import { IconBrain, IconDna, IconHeartbeat } from '@tabler/icons-react';

import type { FileBrowserApi } from '../../../components/FileBrowser';
import FileBrowser from '../../../components/FileBrowser';
import { fileApi } from '../../../services/api';

export default function MindTab({
    agentId,
    canEdit,
}: {
    agentId: string;
    canEdit: boolean;
}) {
    const { t } = useTranslation();
    const adapter: FileBrowserApi = {
        list: (path) => fileApi.list(agentId, path),
        read: (path) => fileApi.read(agentId, path),
        write: (path, content) => fileApi.write(agentId, path, content),
        delete: (path) => fileApi.delete(agentId, path),
        downloadUrl: (path) => fileApi.downloadUrl(agentId, path),
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            <div>
                <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <IconDna size={18} stroke={1.8} /> {t('agent.soul.title')}
                </h3>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {t('agent.mind.soulDesc', 'Core identity, personality, and behavior boundaries.')}
                </p>
                <FileBrowser api={adapter} singleFile="soul.md" title="" features={{ edit: canEdit }} />
            </div>

            <div>
                <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <IconBrain size={18} stroke={1.8} /> {t('agent.memory.title')}
                </h3>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {t('agent.mind.memoryDesc', 'Persistent memory accumulated through conversations and experiences.')}
                </p>
                <FileBrowser api={adapter} rootPath="memory" readOnly features={{}} />
            </div>

            <div>
                <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <IconHeartbeat size={18} stroke={1.8} /> {t('agent.mind.heartbeatTitle', 'Heartbeat')}
                </h3>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {t('agent.mind.heartbeatDesc', 'Instructions for periodic awareness checks. The agent reads this file during each heartbeat.')}
                </p>
                <FileBrowser api={adapter} singleFile="HEARTBEAT.md" title="" features={{ edit: canEdit }} />
            </div>
        </div>
    );
}
