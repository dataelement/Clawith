import { useState, type CSSProperties } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { IconArrowRight, IconBriefcase2, IconCheck, IconCopy, IconUsersGroup } from '@tabler/icons-react';
import { projectApi } from '../services/projectApi';
import type { TeamPlan } from '../types/project';

export default function Projects() {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [name, setName] = useState('');
    const [requirements, setRequirements] = useState('');
    const [plan, setPlan] = useState<TeamPlan | null>(null);
    const [error, setError] = useState('');
    const [copied, setCopied] = useState(false);
    const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: projectApi.list });
    const planMutation = useMutation({
        mutationFn: projectApi.buildTeamPlan,
        onSuccess: (nextPlan) => { setPlan(nextPlan); setError(''); setCopied(false); },
        onError: (reason) => setError(reason instanceof Error ? reason.message : '团队方案生成失败，请稍后重试。'),
    });
    const createMutation = useMutation({
        mutationFn: projectApi.create,
        onSuccess: (project) => {
            queryClient.invalidateQueries({ queryKey: ['projects'] });
            queryClient.invalidateQueries({ queryKey: ['groups'] });
            if (project.group_id) navigate(`/groups/${project.group_id}`);
        },
        onError: (reason) => setError(reason instanceof Error ? reason.message : '项目群创建失败，未完成创建。请稍后重试。'),
    });
    const repairMutation = useMutation({
        mutationFn: projectApi.provision,
        onSuccess: (project) => {
            queryClient.invalidateQueries({ queryKey: ['projects'] });
            queryClient.invalidateQueries({ queryKey: ['groups'] });
            setError('');
            if (project.group_id) navigate(`/groups/${project.group_id}`);
        },
        onError: (reason) => setError(reason instanceof Error ? reason.message : '团队就绪修复失败，请稍后重试。'),
    });
    const buildPlan = () => {
        if (!name.trim() || !requirements.trim()) {
            setError('请填写项目名称和需求。');
            return;
        }
        planMutation.mutate({ name, requirements });
    };
    const createProject = () => {
        if (plan) createMutation.mutate({ name, requirements, team_plan: plan });
    };
    const copyWakeUpMessage = async () => {
        if (!plan?.wake_up_message) return;
        await navigator.clipboard?.writeText(plan.wake_up_message);
        setCopied(true);
    };

    return <div style={{ maxWidth: 1080, margin: '0 auto', padding: '36px 32px 56px' }}>
        <div style={{ marginBottom: 28 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--accent, #635bff)' }}>
                <IconBriefcase2 size={24} /><span style={{ fontWeight: 700 }}>项目流程</span>
            </div>
            <h1 style={{ margin: '10px 0 8px', fontSize: 28 }}>先组建团队，再创建项目群</h1>
            <p style={{ margin: 0, color: 'var(--text-secondary, #6b7280)' }}>HR 招聘 Agent 会根据你的实际需求招聘团队，并挑选最适合的项目群主；确认后按自定义成员流程创建全部智能体。</p>
        </div>
        <section style={cardStyle}>
            <h2 style={headingStyle}>1. 描述项目</h2>
            <label style={labelStyle}>项目名称<input value={name} onChange={(e) => { setName(e.target.value); setPlan(null); }} placeholder="例如：Q3 移动端改版" style={inputStyle} /></label>
            <label style={{ ...labelStyle, marginTop: 14 }}>需求<textarea value={requirements} onChange={(e) => { setRequirements(e.target.value); setPlan(null); }} placeholder="说明目标、交付物、边界、时间要求和已有资料…" rows={5} style={{ ...inputStyle, resize: 'vertical' }} /></label>
            {error && <p style={{ color: '#dc2626', marginBottom: 0 }}>{error}</p>}
            <button onClick={buildPlan} disabled={planMutation.isPending} style={primaryStyle}>{planMutation.isPending ? 'HR 招聘 Agent 正在组建团队…' : '由 HR 招聘 Agent 组建团队'} <IconArrowRight size={16} /></button>
        </section>
        {plan && <section style={{ ...cardStyle, marginTop: 20 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' }}><div><h2 style={headingStyle}>2. 预览并确认团队</h2><p style={{ color: 'var(--text-secondary, #6b7280)', margin: 0 }}>团队已由 {plan.planner_name} 生成。群主由项目需要决定，负责分派任务并向你汇报。</p></div><span style={tagStyle}>{plan.roles.length} 位成员</span></div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, marginTop: 20 }}>
                {plan.roles.map((role) => <div key={role.key} style={{ border: '1px solid var(--border, #e5e7eb)', borderRadius: 10, padding: 14 }}><div style={{ display: 'flex', gap: 7, alignItems: 'center', fontWeight: 650 }}>{role.is_group_leader && <IconCheck size={16} color="#16a34a" />}{role.name}{role.is_group_leader && <span style={tagStyle}>项目群主</span>}</div><p style={{ color: 'var(--text-secondary, #6b7280)', fontSize: 13, marginBottom: 0 }}>{role.role_description}</p></div>)}
            </div>
            <div style={{ marginTop: 20, border: '1px solid var(--border, #e5e7eb)', borderRadius: 10, padding: 14, background: 'var(--bg-secondary, #f8fafc)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}><strong>唤醒团队文案</strong><button type="button" onClick={() => void copyWakeUpMessage()} style={secondaryStyle}><IconCopy size={14} /> {copied ? '已复制' : '复制文案'}</button></div>
                <pre style={{ margin: '12px 0 0', whiteSpace: 'pre-wrap', font: 'inherit', lineHeight: 1.6, color: 'var(--text-secondary, #475569)' }}>{plan.wake_up_message}</pre>
            </div>
            <button onClick={createProject} disabled={createMutation.isPending} style={{ ...primaryStyle, marginTop: 20 }}>{createMutation.isPending ? '正在创建智能体和项目群…' : '确认并创建项目群'} <IconUsersGroup size={16} /></button>
        </section>}
        {projects.length > 0 && <section style={{ ...cardStyle, marginTop: 20 }}><h2 style={headingStyle}>我的项目</h2>{projects.map((project) => <div key={project.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, borderTop: '1px solid var(--border, #e5e7eb)', padding: '13px 0' }}><div><strong>{project.name}</strong><div style={{ color: 'var(--text-secondary, #6b7280)', fontSize: 13, marginTop: 4 }}>{project.members.length} 位成员 · {project.status === 'active' ? '群主已就绪' : project.status}{project.failure_reason ? ` · ${project.failure_reason}` : ''}</div></div><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{project.group_id && <button onClick={() => navigate(`/groups/${project.group_id}`)} style={secondaryStyle}>进入项目群</button>}<button onClick={() => repairMutation.mutate(project.id)} disabled={repairMutation.isPending} style={secondaryStyle}>{repairMutation.isPending ? '正在检查团队…' : '检查并修复团队就绪'}</button></div></div>)}</section>}
    </div>;
}

const cardStyle: CSSProperties = { background: 'var(--bg-card, #fff)', border: '1px solid var(--border, #e5e7eb)', borderRadius: 14, padding: 22, boxShadow: '0 1px 2px rgba(0,0,0,.03)' };
const headingStyle: CSSProperties = { margin: '0 0 10px', fontSize: 18 };
const labelStyle: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 7, fontWeight: 600, fontSize: 14 };
const inputStyle: CSSProperties = { font: 'inherit', fontWeight: 400, border: '1px solid var(--border, #d1d5db)', borderRadius: 8, padding: '10px 11px', background: 'var(--bg-primary, #fff)', color: 'inherit' };
const primaryStyle: CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 8, border: 0, borderRadius: 8, padding: '10px 14px', marginTop: 18, color: '#fff', background: 'var(--accent, #635bff)', fontWeight: 650, cursor: 'pointer' };
const secondaryStyle: CSSProperties = { border: '1px solid var(--border, #d1d5db)', borderRadius: 8, padding: '7px 10px', background: 'transparent', color: 'inherit', cursor: 'pointer' };
const tagStyle: CSSProperties = { fontSize: 11, fontWeight: 650, padding: '3px 6px', borderRadius: 999, color: '#166534', background: '#dcfce7', whiteSpace: 'nowrap' };
