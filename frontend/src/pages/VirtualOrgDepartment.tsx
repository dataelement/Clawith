import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { virtualOrgApi } from '../services/api';

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

function AgentRow({ agent }: { agent: any }) {
    return (
        <Link
            to={`/agents/${agent.id}`}
            style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: '12px',
                padding: '10px 0',
                borderBottom: '1px solid var(--border-subtle)',
                textDecoration: 'none',
                color: 'inherit',
            }}
        >
            <div>
                <div style={{ fontSize: '14px', fontWeight: 600 }}>{agent.name}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{agent.title}</div>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>{agent.level} · {agent.org_bucket}</div>
        </Link>
    );
}

export default function VirtualOrgDepartment() {
    const { slug = '' } = useParams();
    const { data: departments = [], isLoading: departmentsLoading } = useQuery({
        queryKey: ['virtual-org-departments'],
        queryFn: virtualOrgApi.departments,
    });
    const department = departments.find((item) => item.slug === slug);

    const { data: coreAgents, isLoading: coreLoading } = useQuery({
        queryKey: ['virtual-org-department-core', department?.id],
        queryFn: () => virtualOrgApi.agents({ departmentId: department?.id, orgBucket: 'core', pageSize: 100 }),
        enabled: !!department?.id,
    });

    const { data: expertAgents, isLoading: expertLoading } = useQuery({
        queryKey: ['virtual-org-department-expert', department?.id],
        queryFn: () => virtualOrgApi.agents({ departmentId: department?.id, orgBucket: 'expert', pageSize: 100 }),
        enabled: !!department?.id,
    });

    if (departmentsLoading || coreLoading || expertLoading) {
        return <div style={pageStyle}>部门详情加载中...</div>;
    }

    if (!department) {
        return <div style={pageStyle}>未找到该部门。</div>;
    }

    return (
        <div style={pageStyle}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <Link to="/virtual-org" style={{ fontSize: '13px', color: 'var(--text-tertiary)', textDecoration: 'none' }}>← 返回虚拟组织</Link>
                <div style={{ fontSize: '28px', fontWeight: 800 }}>{department.name}</div>
                <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>查看该部门的核心岗位与专家支持。</div>
            </div>

            <section style={cardStyle}>
                <div style={{ fontSize: '16px', fontWeight: 700, marginBottom: '8px' }}>核心岗位</div>
                {(coreAgents?.items || []).length === 0 ? (
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>暂无核心岗位。</div>
                ) : (
                    (coreAgents?.items || []).map((agent) => <AgentRow key={agent.id} agent={agent} />)
                )}
            </section>

            <section style={cardStyle}>
                <div style={{ fontSize: '16px', fontWeight: 700, marginBottom: '8px' }}>专家支持</div>
                {(expertAgents?.items || []).length === 0 ? (
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>暂无专家支持。</div>
                ) : (
                    (expertAgents?.items || []).map((agent) => <AgentRow key={agent.id} agent={agent} />)
                )}
            </section>
        </div>
    );
}
