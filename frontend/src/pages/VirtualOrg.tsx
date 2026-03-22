import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { virtualOrgApi } from '../services/api';
import type { VirtualOrgAgentSummary, VirtualOrgDepartment } from '../types';
import { useAuthStore } from '../stores';

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
    boxShadow: '0 8px 30px rgba(0,0,0,0.08)',
};

function AgentChip({ agent, clickable = false }: { agent: VirtualOrgAgentSummary; clickable?: boolean }) {
    const content = (
        <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            padding: '10px 12px',
            borderRadius: '10px',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
        }}>
            <div style={{ fontSize: '13px', fontWeight: 600 }}>{agent.name}</div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{agent.title}</div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{agent.level} · {agent.org_bucket}</div>
        </div>
    );

    if (!clickable) return content;

    return (
        <Link to={`/agents/${agent.id}`} style={{ textDecoration: 'none', color: 'inherit' }}>
            {content}
        </Link>
    );
}

function DepartmentCard({ department }: { department: VirtualOrgDepartment }) {
    return (
        <Link
            to={`/virtual-org/departments/${department.slug}`}
            style={{
                ...cardStyle,
                textDecoration: 'none',
                color: 'inherit',
                display: 'flex',
                flexDirection: 'column',
                gap: '12px',
            }}
        >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
                <div>
                    <div style={{ fontSize: '15px', fontWeight: 700 }}>{department.name}</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        {department.leader ? `负责人：${department.leader.name}` : '负责人待定'}
                    </div>
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{department.core_agents.length} 个核心岗位</div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: '10px' }}>
                {department.core_agents.slice(0, 4).map((agent) => <AgentChip key={agent.id} agent={agent} />)}
            </div>

            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                专家支持：{department.expert_count} · 点击查看部门详情
            </div>
        </Link>
    );
}

function SectionLinkCard({
    to,
    title,
    subtitle,
    children,
}: {
    to: string;
    title: string;
    subtitle?: string;
    children: React.ReactNode;
}) {
    return (
        <Link
            to={to}
            style={{
                ...cardStyle,
                textDecoration: 'none',
                color: 'inherit',
                display: 'flex',
                flexDirection: 'column',
                gap: '12px',
            }}
        >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
                <div style={{ fontSize: '16px', fontWeight: 700 }}>{title}</div>
                {subtitle && <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{subtitle}</div>}
            </div>
            {children}
            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>点击查看详情</div>
        </Link>
    );
}

export default function VirtualOrg() {
    const user = useAuthStore((state) => state.user);
    const { data, isLoading, error } = useQuery({
        queryKey: ['virtual-org-overview'],
        queryFn: virtualOrgApi.overview,
    });

    if (isLoading) {
        return <div style={pageStyle}>虚拟组织加载中...</div>;
    }

    if (error || !data) {
        return <div style={pageStyle}>虚拟组织加载失败。</div>;
    }

    return (
        <div style={pageStyle}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ fontSize: '28px', fontWeight: 800 }}>虚拟公司组织</div>
                <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
                    先看高管层和核心部门，再按需展开专家库。
                </div>
                {user && ['platform_admin', 'org_admin'].includes(user.role) && (
                    <div>
                        <Link to="/virtual-org/admin" style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                            进入组织编排台 →
                        </Link>
                    </div>
                )}
            </div>

            <SectionLinkCard to="/virtual-org/departments/executive" title="高管层">
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '10px' }}>
                    {data.executives.map((agent) => <AgentChip key={agent.id} agent={agent} clickable />)}
                </div>
            </SectionLinkCard>

            <section style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div style={{ fontSize: '16px', fontWeight: 700 }}>核心部门</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '14px' }}>
                    {data.departments.map((department) => <DepartmentCard key={department.id} department={department} />)}
                </div>
            </section>

            <SectionLinkCard to="/virtual-org/departments/expert-pool" title="专家库" subtitle={`${data.expert_pool.count} 位专家`}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '10px' }}>
                    {data.expert_pool.agents.slice(0, 12).map((agent) => <AgentChip key={agent.id} agent={agent} clickable />)}
                </div>
            </SectionLinkCard>
        </div>
    );
}
