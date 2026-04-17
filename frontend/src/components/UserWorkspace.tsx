import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { userWorkspaceApi } from '../services/userWorkspaceApi';
import FileBrowser from '../components/FileBrowser';
import type { FileBrowserApi } from '../components/FileBrowser';

interface UserWorkspaceProps {
  agentId: string;
  currentUserId: string;
  isCreator: boolean;
  isAdmin: boolean;
}

export default function UserWorkspace({ agentId, currentUserId, isCreator, isAdmin }: UserWorkspaceProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const canManage = isCreator || isAdmin;

  // Selected user (for admin/creator to view other users' workspaces)
  const [selectedUserId, setSelectedUserId] = useState<string>(currentUserId);

  // Fetch list of users from sessions (same as chat tab)
  const { data: sessionsData, isLoading: sessionsLoading } = useQuery({
    queryKey: ['agent-sessions-all', agentId],
    queryFn: async () => {
      const token = localStorage.getItem('token');
      const res = await fetch(`/api/agents/${agentId}/sessions?scope=all`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return [];
      return res.json();
    },
    enabled: canManage,
  });

  // Extract unique users from sessions
  const usersData = React.useMemo(() => {
    if (!sessionsData || !Array.isArray(sessionsData)) return { users: [] };
    const userMap = new Map<string, { id: string; display_name: string }>();
    sessionsData.forEach((session: any) => {
      if (session.user_id) {
        const userId = session.user_id;
        if (!userMap.has(userId)) {
          userMap.set(userId, {
            id: userId,
            display_name: session.username || `User ${userId.slice(0, 8)}`,
          });
        }
      }
    });
    return { users: Array.from(userMap.values()) };
  }, [sessionsData]);

  // Fetch user's files
  const { data: filesData, refetch: refetchFiles } = useQuery({
    queryKey: ['user-files', agentId, selectedUserId],
    queryFn: () => userWorkspaceApi.listFiles(agentId, selectedUserId),
  });

  // Fetch user's memory
  const { data: memoryData, refetch: refetchMemory } = useQuery({
    queryKey: ['user-memory', agentId, selectedUserId],
    queryFn: () => userWorkspaceApi.getMemory(agentId, selectedUserId),
  });

  // Update memory mutation
  const updateMemoryMutation = useMutation({
    mutationFn: ({ userId, content }: { userId: string; content: string }) =>
      userWorkspaceApi.updateMemory(agentId, userId, content),
    onSuccess: () => {
      refetchMemory();
    },
  });

  // Memory editor state
  const [memoryContent, setMemoryContent] = useState(memoryData?.content || '');
  const [isEditingMemory, setIsEditingMemory] = useState(false);

  // Sync memory content when data changes
  React.useEffect(() => {
    if (memoryData?.content !== undefined) {
      setMemoryContent(memoryData.content);
    }
  }, [memoryData?.content]);

  const handleSaveMemory = async () => {
    await updateMemoryMutation.mutateAsync({
      userId: selectedUserId,
      content: memoryContent,
    });
    setIsEditingMemory(false);
  };

  // File browser adapter for user files
  const fileAdapter: any = {
    list: async (path: string) => {
      const res = await userWorkspaceApi.listFiles(agentId, selectedUserId, path);
      return res.files.map((f: any) => ({ ...f, isDirectory: false }));
    },
    read: async (path: string) => {
      const content = await userWorkspaceApi.readFile(agentId, selectedUserId, path);
      return { content };
    },
    write: (path: string, content: string) => userWorkspaceApi.writeFile(agentId, selectedUserId, path, content),
    delete: (path: string) => userWorkspaceApi.deleteFile(agentId, selectedUserId, path),
    upload: (file: File, path: string, onProgress?: (p: number) => void) => userWorkspaceApi.uploadFile(agentId, selectedUserId, file, path, onProgress),
    downloadUrl: (path: string) => userWorkspaceApi.getDownloadUrl(agentId, selectedUserId, path),
  };

  // Get display name for selected user
  const getUserName = (userId: string) => {
    if (userId === currentUserId) {
      return t('userWorkspace.myself', '我自己');
    }
    const user = usersData?.users.find(u => u.id === userId);
    return user?.display_name || userId;
  };

  return (
    <div>
      {/* Header with user selector (for admin/creator) */}
      <div style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <div>
            <h3 style={{ marginBottom: '4px' }}>{t('userWorkspace.title', '个人空间')}</h3>
            <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
              {t('userWorkspace.description', '每个用户都有独立的个人空间，用于存储私有文件和记忆')}
            </p>
          </div>
          
          {/* User selector for admin/creator */}
          {canManage && (
            <div style={{ minWidth: '200px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                {t('userWorkspace.viewingAs', '查看用户:')}
              </label>
              <select
                className="input"
                value={selectedUserId}
                onChange={(e) => setSelectedUserId(e.target.value)}
                style={{ fontSize: '13px' }}
              >
                {usersData?.users.map((user: { id: string; display_name: string }) => (
                  <option key={user.id} value={user.id}>
                    {user.id === currentUserId ? '👤 ' : ''}{user.display_name || user.id}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Info banner */}
        <div style={{
          padding: '12px 16px',
          background: 'var(--accent-subtle)',
          borderRadius: '8px',
          fontSize: '12px',
          color: 'var(--text-secondary)',
          marginBottom: '20px',
        }}>
          <strong>💡 {t('common.tip', '提示')}:</strong> {t('userWorkspace.tip', '个人空间中的文件和记忆对该用户私有，其他用户无法访问。管理员和创建者可以查看所有用户的个人空间。')}
        </div>
      </div>

      {/* Two columns: Files and Memory */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        {/* Left: User Files */}
        <div className="card">
          <h4 style={{ marginBottom: '12px' }}>
            📁 {t('userWorkspace.myFiles', '我的文件')}
          </h4>
          <FileBrowser
            api={fileAdapter}
            rootPath=""
            features={{
              newFile: true,
              edit: true,
              delete: canManage || selectedUserId === currentUserId,
              newFolder: true,
              upload: true,
              directoryNavigation: true,
            }}
            title={t('userWorkspace.files', '文件')}
          />
        </div>

        {/* Right: User Memory */}
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <h4>
              📝 {t('userWorkspace.myMemory', '个人记忆')}
            </h4>
            <button
              className="btn btn-secondary"
              onClick={() => setIsEditingMemory(!isEditingMemory)}
              style={{ fontSize: '12px', padding: '4px 12px' }}
            >
              {isEditingMemory ? t('common.cancel', '取消') : t('common.edit', '编辑')}
            </button>
          </div>

          {isEditingMemory ? (
            <>
              <textarea
                className="input"
                rows={12}
                value={memoryContent}
                onChange={(e) => setMemoryContent(e.target.value)}
                placeholder={t('userWorkspace.memoryPlaceholder', '记录与该 Agent 的私人对话记忆、偏好设置等...')}
                style={{
                  width: '100%',
                  resize: 'vertical',
                  fontFamily: 'inherit',
                  fontSize: '13px',
                }}
              />
              <div style={{ marginTop: '12px', display: 'flex', gap: '8px' }}>
                <button
                  className="btn btn-primary"
                  onClick={handleSaveMemory}
                  disabled={updateMemoryMutation.isPending}
                  style={{ fontSize: '13px' }}
                >
                  {updateMemoryMutation.isPending ? t('common.saving', '保存中...') : t('common.save', '保存')}
                </button>
                <button
                  className="btn btn-secondary"
                  onClick={() => {
                    setMemoryContent(memoryData?.content || '');
                    setIsEditingMemory(false);
                  }}
                  style={{ fontSize: '13px' }}
                >
                  {t('common.cancel', '取消')}
                </button>
              </div>
            </>
          ) : (
            <div
              style={{
                fontSize: '13px',
                lineHeight: 1.6,
                color: 'var(--text-secondary)',
                minHeight: '200px',
                whiteSpace: 'pre-wrap',
              }}
            >
              {memoryData?.content || (
                <span style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                  {t('userWorkspace.noMemory', '暂无个人记忆')}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
