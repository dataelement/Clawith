import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconRobot } from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import GroupTextFileEditor from './GroupTextFileEditor';
import type { GroupMember } from '../../types/group';

/**
 * Group memory is per (agent, group): each agent keeps its own memory.md for this group, loaded
 * only when that agent is mentioned here. Humans may read, edit and delete any of them.
 */
export default function GroupMemoryTab({
    groupId,
    members,
}: {
    groupId: string;
    members: GroupMember[];
}) {
    const { t } = useTranslation();
    const agents = members.filter((member) => member.participant_type === 'agent');
    const [agentRefId, setAgentRefId] = useState<string | undefined>(agents[0]?.participant_ref_id);

    useEffect(() => {
        if (!agentRefId && agents.length > 0) setAgentRefId(agents[0].participant_ref_id);
    }, [agents, agentRefId]);

    if (agents.length === 0) {
        return (
            <div className="group-empty-hint">
                {t('groups.noAgentsForMemory', '群里还没有智能体。邀请一个之后，它在这个群的记忆会显示在这里。')}
            </div>
        );
    }

    return (
        <>
            <div className="group-memory-agents">
                {agents.map((agent) => (
                    <button
                        key={agent.participant_id}
                        type="button"
                        className={`group-memory-agent ${agent.participant_ref_id === agentRefId ? 'active' : ''}`}
                        onClick={() => setAgentRefId(agent.participant_ref_id)}
                    >
                        <IconRobot size={13} stroke={1.6} />
                        {agent.display_name}
                    </button>
                ))}
            </div>

            {agentRefId && (
                <GroupTextFileEditor
                    queryKey={['group-agent-memory', groupId, agentRefId]}
                    note={t('groups.memoryNote', '这是该智能体在本群的长期记忆，只在它于本群被 @ 时加载。')}
                    placeholder={t('groups.memoryPlaceholder', '这个智能体在本群需要长期记住的内容...')}
                    load={() => groupApi.agentMemory(groupId, agentRefId)}
                    save={(content, token) => groupApi.saveAgentMemory(groupId, agentRefId, content, token)}
                    onDelete={() => groupApi.deleteAgentMemory(groupId, agentRefId)}
                    deleteLabel={t('groups.memoryClear', '清空记忆')}
                />
            )}
        </>
    );
}
