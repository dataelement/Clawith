import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../stores';
import { virtualOrgApi } from '../services/api';
import type { VirtualOrgAgentSummary, VirtualOrgDepartment } from '../types';

const pageStyle: React.CSSProperties = {
    padding: '24px',
    display: 'flex',
    flexDirection: 'column',
    gap: '20px',
};

const cardStyle: React.CSSProperties = {
    background: 'var(--bg-primary)',
    border: '1px solid var(--border-subtle)',
    borderRadius: '14px',
    padding: '16px',
};

export default function VirtualOrgAdmin() {
    const queryClient = useQueryClient();
    const user = useAuthStore((state) => state.user);
    const isAdmin = !!user && ['platform_admin', 'org_admin'].includes(user.role);

    const [selectedDepartmentId, setSelectedDepartmentId] = useState('');
    const [selectedBucket, setSelectedBucket] = useState<'core' | 'expert' | 'all'>('all');
    const [selectedAgentId, setSelectedAgentId] = useState('');
    const [departmentForm, setDepartmentForm] = useState({ name: '', slug: '', sort_order: 0, org_level: 'department', is_core: true });
    const [agentForm, setAgentForm] = useState({ department_id: '', level: 'L3', org_bucket: 'core', title: '', tags: '' });

    const { data: departments = [] } = useQuery({
        queryKey: ['virtual-org-departments-admin'],
        queryFn: virtualOrgApi.departments,
        enabled: isAdmin,
    });

    useEffect(() => {
        if (!selectedDepartmentId && departments.length > 0) {
            setSelectedDepartmentId(departments[0].id);
        }
    }, [departments, selectedDepartmentId]);

    const { data: agentsData, isLoading: agentsLoading } = useQuery({
        queryKey: ['virtual-org-admin-agents', selectedDepartmentId, selectedBucket],
        queryFn: () => virtualOrgApi.agents({
            departmentId: selectedDepartmentId || undefined,
            orgBucket: selectedBucket === 'all' ? undefined : selectedBucket,
            pageSize: 200,
        }),
        enabled: isAdmin && !!selectedDepartmentId,
    });

    const agents = agentsData?.items || [];

    const selectedAgent = useMemo(
        () => agents.find((agent) => agent.id === selectedAgentId) || null,
        [agents, selectedAgentId],
    );

    useEffect(() => {
        if (selectedAgent) {
            setAgentForm({
                department_id: selectedAgent.department_id,
                level: selectedAgent.level,
                org_bucket: selectedAgent.org_bucket,
                title: selectedAgent.title,
                tags: selectedAgent.tags.join(', '),
            });
        }
    }, [selectedAgent]);

    const refreshVirtualOrg = async () => {
        await queryClient.invalidateQueries({ queryKey: ['virtual-org-overview'] });
        await queryClient.invalidateQueries({ queryKey: ['virtual-org-departments'] });
        await queryClient.invalidateQueries({ queryKey: ['virtual-org-departments-admin'] });
        await queryClient.invalidateQueries({ queryKey: ['virtual-org-admin-agents'] });
    };

    const bootstrapMutation = useMutation({
        mutationFn: (force: boolean) => virtualOrgApi.bootstrap(force),
        onSuccess: refreshVirtualOrg,
    });

    const createDepartmentMutation = useMutation({
        mutationFn: virtualOrgApi.createDepartment,
        onSuccess: async () => {
            setDepartmentForm({ name: '', slug: '', sort_order: 0, org_level: 'department', is_core: true });
            await refreshVirtualOrg();
        },
    });

    const updateDepartmentMutation = useMutation({
        mutationFn: ({ departmentId, data }: { departmentId: string; data: Partial<VirtualOrgDepartment> }) =>
            virtualOrgApi.updateDepartment(departmentId, data),
        onSuccess: refreshVirtualOrg,
    });

    const updateAgentMutation = useMutation({
        mutationFn: ({ agentId, data }: { agentId: string; data: any }) => virtualOrgApi.updateAgent(agentId, data),
        onSuccess: refreshVirtualOrg,
    });

    if (!isAdmin) {
        return <div style={pageStyle}>只有管理员可以维护虚拟组织。</div>;
    }

    return (
        <div style={pageStyle}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <Link to="/virtual-org" style={{ fontSize: '13px', color: 'var(--text-tertiary)', textDecoration: 'none' }}>← 返回虚拟组织</Link>
                <div style={{ fontSize: '28px', fontWeight: 800 }}>组织编排台</div>
                <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>维护部门排序、Agent 归属、层级和专家库归类。</div>
            </div>

            <section style={cardStyle}>
                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
                    <button className="btn btn-primary" onClick={() => bootstrapMutation.mutate(false)} disabled={bootstrapMutation.isPending}>执行 bootstrap</button>
                    <button className="btn btn-secondary" onClick={() => bootstrapMutation.mutate(true)} disabled={bootstrapMutation.isPending}>强制重建组织</button>
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>用于批量重建核心组织与专家库归类。</span>
                </div>
            </section>

            <section style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 380px) 1fr', gap: '16px' }}>
                <div style={{ ...cardStyle, display: 'flex', flexDirection: 'column', gap: '14px' }}>
                    <div style={{ fontSize: '16px', fontWeight: 700 }}>部门管理</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        <input className="form-input" placeholder="部门名称" value={departmentForm.name} onChange={(e) => setDepartmentForm((prev) => ({ ...prev, name: e.target.value }))} />
                        <input className="form-input" placeholder="slug" value={departmentForm.slug} onChange={(e) => setDepartmentForm((prev) => ({ ...prev, slug: e.target.value }))} />
                        <input className="form-input" type="number" placeholder="sort_order" value={departmentForm.sort_order} onChange={(e) => setDepartmentForm((prev) => ({ ...prev, sort_order: Number(e.target.value) }))} />
                        <button className="btn btn-primary" onClick={() => createDepartmentMutation.mutate(departmentForm)} disabled={createDepartmentMutation.isPending}>新建部门</button>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {departments.map((department) => (
                            <div key={department.id} style={{ padding: '10px', borderRadius: '10px', background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', gap: '8px' }}>
                                    <div>
                                        <div style={{ fontSize: '13px', fontWeight: 600 }}>{department.name}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{department.slug}</div>
                                    </div>
                                    <input
                                        className="form-input"
                                        style={{ width: '86px' }}
                                        type="number"
                                        value={department.sort_order}
                                        onChange={(e) => updateDepartmentMutation.mutate({ departmentId: department.id, data: { sort_order: Number(e.target.value) } })}
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                <div style={{ ...cardStyle, display: 'flex', flexDirection: 'column', gap: '14px' }}>
                    <div style={{ fontSize: '16px', fontWeight: 700 }}>Agent 归属管理</div>

                    <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                        <select className="form-input" value={selectedDepartmentId} onChange={(e) => setSelectedDepartmentId(e.target.value)}>
                            {departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
                        </select>
                        <select className="form-input" value={selectedBucket} onChange={(e) => setSelectedBucket(e.target.value as 'core' | 'expert' | 'all')}>
                            <option value="all">全部</option>
                            <option value="core">核心组织</option>
                            <option value="expert">专家库</option>
                        </select>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 300px) 1fr', gap: '14px' }}>
                        <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '12px', overflow: 'hidden' }}>
                            {agentsLoading ? (
                                <div style={{ padding: '16px' }}>加载中...</div>
                            ) : agents.length === 0 ? (
                                <div style={{ padding: '16px' }}>当前筛选下暂无 Agent。</div>
                            ) : (
                                agents.map((agent) => (
                                    <button
                                        key={agent.id}
                                        onClick={() => setSelectedAgentId(agent.id)}
                                        style={{
                                            width: '100%',
                                            textAlign: 'left',
                                            padding: '12px 14px',
                                            border: 'none',
                                            borderBottom: '1px solid var(--border-subtle)',
                                            background: selectedAgentId === agent.id ? 'var(--bg-secondary)' : 'transparent',
                                            cursor: 'pointer',
                                        }}
                                    >
                                        <div style={{ fontSize: '13px', fontWeight: 600 }}>{agent.name}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{agent.title}</div>
                                    </button>
                                ))
                            )}
                        </div>

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                            {selectedAgent ? (
                                <>
                                    <div style={{ fontSize: '14px', fontWeight: 700 }}>{selectedAgent.name}</div>
                                    <select className="form-input" value={agentForm.department_id} onChange={(e) => setAgentForm((prev) => ({ ...prev, department_id: e.target.value }))}>
                                        {departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
                                    </select>
                                    <input className="form-input" value={agentForm.title} onChange={(e) => setAgentForm((prev) => ({ ...prev, title: e.target.value }))} placeholder="岗位名称" />
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                                        <select className="form-input" value={agentForm.level} onChange={(e) => setAgentForm((prev) => ({ ...prev, level: e.target.value }))}>
                                            {['L1', 'L2', 'L3', 'L4', 'L5'].map((level) => <option key={level} value={level}>{level}</option>)}
                                        </select>
                                        <select className="form-input" value={agentForm.org_bucket} onChange={(e) => setAgentForm((prev) => ({ ...prev, org_bucket: e.target.value }))}>
                                            <option value="core">核心组织</option>
                                            <option value="expert">专家库</option>
                                        </select>
                                    </div>
                                    <input className="form-input" value={agentForm.tags} onChange={(e) => setAgentForm((prev) => ({ ...prev, tags: e.target.value }))} placeholder="标签，逗号分隔" />
                                    <button
                                        className="btn btn-primary"
                                        onClick={() => updateAgentMutation.mutate({
                                            agentId: selectedAgent.id,
                                            data: {
                                                department_id: agentForm.department_id,
                                                title: agentForm.title,
                                                level: agentForm.level,
                                                org_bucket: agentForm.org_bucket,
                                                tags: agentForm.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
                                            },
                                        })}
                                        disabled={updateAgentMutation.isPending}
                                    >
                                        保存 Agent 归属
                                    </button>
                                </>
                            ) : (
                                <div style={{ color: 'var(--text-tertiary)' }}>先从左侧选择一个 Agent。</div>
                            )}
                        </div>
                    </div>
                </div>
            </section>
        </div>
    );
}
