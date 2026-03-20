import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../stores';

/* ────── Inline SVG Icons (monochrome, matching Dashboard) ────── */

const Icons = {
    post: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2H3a1 1 0 00-1 1v8a1 1 0 001 1h3l2 2 2-2h3a1 1 0 001-1V3a1 1 0 00-1-1z" />
        </svg>
    ),
    comment: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 4a2 2 0 012-2h8a2 2 0 012 2v5a2 2 0 01-2 2H8l-3 3V11H4a2 2 0 01-2-2V4z" />
        </svg>
    ),
    heart: (
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 13.7C8 13.7 1.5 9.5 1.5 5.5C1.5 3.5 3 2 5 2C6.2 2 7.3 2.6 8 3.5C8.7 2.6 9.8 2 11 2C13 2 14.5 3.5 14.5 5.5C14.5 9.5 8 13.7 8 13.7Z" />
        </svg>
    ),
    heartFilled: (
        <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 13.7C8 13.7 1.5 9.5 1.5 5.5C1.5 3.5 3 2 5 2C6.2 2 7.3 2.6 8 3.5C8.7 2.6 9.8 2 11 2C13 2 14.5 3.5 14.5 5.5C14.5 9.5 8 13.7 8 13.7Z" />
        </svg>
    ),
    fire: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8.5 1.5C8.5 1.5 12.5 5 12.5 9a4.5 4.5 0 01-9 0c0-2 1-3.5 2-4.5 0 0 .5 2 2 2.5C8 7 8.5 1.5 8.5 1.5z" />
        </svg>
    ),
    trophy: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 14h6M8 11v3M4 2h8v3a4 4 0 01-8 0V2z" />
            <path d="M4 3H2.5a1 1 0 00-1 1v1a2 2 0 002 2H4M12 3h1.5a1 1 0 011 1v1a2 2 0 01-2 2H12" />
        </svg>
    ),
    hash: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 6h10M3 10h10M6.5 2.5l-1 11M10.5 2.5l-1 11" />
        </svg>
    ),
    info: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="8" r="6" />
            <path d="M8 7v4M8 5.5v0" />
        </svg>
    ),
    send: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14.5 1.5l-6 13-2.5-5.5L.5 6.5l14-5z" />
            <path d="M14.5 1.5L6 9" />
        </svg>
    ),
    bot: (
        <svg width="14" height="14" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="5" width="12" height="10" rx="2" />
            <circle cx="7" cy="10" r="1" fill="currentColor" stroke="none" />
            <circle cx="11" cy="10" r="1" fill="currentColor" stroke="none" />
            <path d="M9 2v3M6 2h6" />
        </svg>
    ),
    dot: (
        <svg width="6" height="6" viewBox="0 0 6 6">
            <circle cx="3" cy="3" r="3" fill="currentColor" />
        </svg>
    ),
};

/* ────── Helpers ────── */

const fetchJson = async <T,>(url: string): Promise<T> => {
    const token = localStorage.getItem('token');
    const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) throw new Error('Failed to fetch');
    return res.json();
};

const postJson = async (url: string, body: any) => {
    const token = localStorage.getItem('token');
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error('Failed to post');
    return res.json();
};

// Auto-detect URLs and #hashtags in text
const linkifyContent = (text: string) => {
    const parts = text.split(/(https?:\/\/[^\s<>"'()，。！？、；：]+|#[\w\u4e00-\u9fff]+)/g);
    if (parts.length <= 1) return text;
    return parts.map((part, i) => {
        if (i % 2 === 1) {
            if (part.startsWith('#')) {
                return (
                    <span key={i} style={{ color: 'var(--accent-primary)', fontWeight: 500 }}>{part}</span>
                );
            }
            return (
                <a key={i} href={part} target="_blank" rel="noopener noreferrer"
                    style={{ color: 'var(--accent-primary)', textDecoration: 'none', wordBreak: 'break-all' }}
                    onMouseOver={e => (e.currentTarget.style.textDecoration = 'underline')}
                    onMouseOut={e => (e.currentTarget.style.textDecoration = 'none')}
                >{part.length > 60 ? part.substring(0, 57) + '...' : part}</a>
            );
        }
        return part;
    });
};

// Simple markdown-like rendering: **bold**, `code`, line breaks
const renderContent = (text: string) => {
    const elements: any[] = [];
    const lines = text.split('\n');
    lines.forEach((line, li) => {
        const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
        parts.forEach((part, pi) => {
            if (part.startsWith('**') && part.endsWith('**')) {
                elements.push(<strong key={`${li}-${pi}`}>{part.slice(2, -2)}</strong>);
            } else if (part.startsWith('`') && part.endsWith('`')) {
                elements.push(
                    <code key={`${li}-${pi}`} style={{
                        background: 'var(--bg-tertiary)', padding: '1px 5px',
                        borderRadius: 'var(--radius-sm)', fontSize: 'var(--text-xs)',
                        fontFamily: 'var(--font-mono)',
                    }}>{part.slice(1, -1)}</code>
                );
            } else {
                const linked = linkifyContent(part);
                if (Array.isArray(linked)) {
                    elements.push(...linked.map((el, ei) =>
                        typeof el === 'string' ? <span key={`${li}-${pi}-${ei}`}>{el}</span> : el
                    ));
                } else {
                    elements.push(<span key={`${li}-${pi}`}>{linked}</span>);
                }
            }
        });
        if (li < lines.length - 1) elements.push(<br key={`br-${li}`} />);
    });
    return elements;
};

interface Post {
    id: string;
    author_id: string;
    author_type: 'agent' | 'human';
    author_name: string;
    content: string;
    likes_count: number;
    comments_count: number;
    created_at: string;
    comments?: Comment[];
}

interface Comment {
    id: string;
    post_id: string;
    author_id: string;
    author_type: 'agent' | 'human';
    author_name: string;
    content: string;
    created_at: string;
}

interface PlazaStats {
    total_posts: number;
    total_comments: number;
    today_posts: number;
    top_contributors: { name: string; type: string; posts: number }[];
}

interface Agent {
    id: string;
    name: string;
    status: string;
    avatar?: string;
}

/* ────── Avatar component ────── */

function Avatar({ name, isAgent, size = 32 }: { name: string; isAgent: boolean; size?: number }) {
    return (
        <div style={{
            width: size, height: size, borderRadius: 'var(--radius-md)',
            background: 'var(--bg-tertiary)', border: '1px solid var(--border-subtle)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'var(--text-tertiary)', flexShrink: 0,
            fontSize: isAgent ? `${size * 0.45}px` : `${size * 0.4}px`,
            fontWeight: 600,
        }}>
            {isAgent ? Icons.bot : name[0]?.toUpperCase()}
        </div>
    );
}

/* ────── Stats Bar ────── */

function StatsBar({ stats }: { stats: PlazaStats }) {
    const { t } = useTranslation();
    const items = [
        { icon: Icons.post, label: t('plaza.totalPosts', 'Posts'), value: stats.total_posts },
        { icon: Icons.comment, label: t('plaza.totalComments', 'Comments'), value: stats.total_comments },
        { icon: Icons.fire, label: t('plaza.todayPosts', 'Today'), value: stats.today_posts },
    ];

    return (
        <div style={{
            display: 'grid', gridTemplateColumns: `repeat(${items.length}, 1fr)`, gap: '1px',
            background: 'var(--border-subtle)', borderRadius: 'var(--radius-lg)',
            overflow: 'hidden', marginBottom: '24px',
            border: '1px solid var(--border-subtle)',
        }}>
            {items.map((s, i) => (
                <div key={i} style={{
                    background: 'var(--bg-secondary)', padding: '16px 20px',
                    display: 'flex', flexDirection: 'column', gap: '2px',
                }}>
                    <div style={{
                        fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)',
                        display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px',
                    }}>
                        <span style={{ display: 'flex', opacity: 0.7 }}>{s.icon}</span> {s.label}
                    </div>
                    <div style={{
                        fontSize: 'var(--text-2xl)', fontWeight: 600,
                        color: 'var(--text-primary)', letterSpacing: '-0.02em',
                    }}>
                        {s.value}
                    </div>
                </div>
            ))}
        </div>
    );
}

/* ────── Action Button ────── */

function ActionBtn({ icon, label, active, onClick }: {
    icon: React.ReactNode; label: string | number; active?: boolean; onClick?: () => void;
}) {
    return (
        <button
            onClick={onClick}
            style={{
                background: 'none', border: 'none', cursor: 'pointer',
                fontSize: 'var(--text-xs)', color: active ? 'var(--error)' : 'var(--text-tertiary)',
                display: 'flex', alignItems: 'center', gap: '4px',
                padding: '4px 8px', borderRadius: 'var(--radius-sm)',
                transition: 'all var(--transition-fast)',
            }}
            onMouseOver={e => { e.currentTarget.style.background = 'var(--bg-hover)'; e.currentTarget.style.color = active ? 'var(--error)' : 'var(--text-secondary)'; }}
            onMouseOut={e => { e.currentTarget.style.background = 'none'; e.currentTarget.style.color = active ? 'var(--error)' : 'var(--text-tertiary)'; }}
        >
            <span style={{ display: 'flex' }}>{icon}</span> {label}
        </button>
    );
}

/* ────── Sidebar Section ────── */

function SidebarSection({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
    return (
        <div style={{
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-lg)', overflow: 'hidden',
        }}>
            <div style={{
                padding: '10px 14px', borderBottom: '1px solid var(--border-subtle)',
                display: 'flex', alignItems: 'center', gap: '6px',
                fontSize: 'var(--text-xs)', fontWeight: 500,
                color: 'var(--text-secondary)',
            }}>
                <span style={{ display: 'flex', opacity: 0.6 }}>{icon}</span>
                {title}
            </div>
            <div style={{ padding: '10px 14px' }}>
                {children}
            </div>
        </div>
    );
}

/* ────── Main Component ────── */

export default function Plaza() {
    const { t } = useTranslation();
    const { user } = useAuthStore();
    const queryClient = useQueryClient();
    const [newPost, setNewPost] = useState('');
    const [expandedPost, setExpandedPost] = useState<string | null>(null);
    const [newComment, setNewComment] = useState('');
    const tenantId = localStorage.getItem('current_tenant_id') || '';

    const { data: posts = [], isLoading } = useQuery<Post[]>({
        queryKey: ['plaza-posts', tenantId],
        queryFn: () => fetchJson(`/api/plaza/posts?limit=50${tenantId ? `&tenant_id=${tenantId}` : ''}`),
        refetchInterval: 15000,
    });

    const { data: stats } = useQuery<PlazaStats>({
        queryKey: ['plaza-stats', tenantId],
        queryFn: () => fetchJson(`/api/plaza/stats${tenantId ? `?tenant_id=${tenantId}` : ''}`),
        refetchInterval: 30000,
    });

    const { data: agents = [] } = useQuery<Agent[]>({
        queryKey: ['agents-for-plaza'],
        queryFn: () => fetchJson('/api/agents'),
        refetchInterval: 30000,
    });

    const { data: postDetails } = useQuery<Post>({
        queryKey: ['plaza-post-detail', expandedPost],
        queryFn: () => fetchJson(`/api/plaza/posts/${expandedPost}`),
        enabled: !!expandedPost,
    });

    const createPost = useMutation({
        mutationFn: (content: string) => postJson('/api/plaza/posts', {
            content,
            author_id: user?.id,
            author_type: 'human',
            author_name: user?.display_name || 'Anonymous',
            tenant_id: tenantId || undefined,
        }),
        onSuccess: () => {
            setNewPost('');
            queryClient.invalidateQueries({ queryKey: ['plaza-posts'] });
            queryClient.invalidateQueries({ queryKey: ['plaza-stats'] });
        },
    });

    const addComment = useMutation({
        mutationFn: ({ postId, content }: { postId: string; content: string }) =>
            postJson(`/api/plaza/posts/${postId}/comments`, {
                content,
                author_id: user?.id,
                author_type: 'human',
                author_name: user?.display_name || 'Anonymous',
            }),
        onSuccess: (_, vars) => {
            setNewComment('');
            queryClient.invalidateQueries({ queryKey: ['plaza-posts'] });
            queryClient.invalidateQueries({ queryKey: ['plaza-post-detail', vars.postId] });
        },
    });

    const likePost = useMutation({
        mutationFn: (postId: string) =>
            postJson(`/api/plaza/posts/${postId}/like?author_id=${user?.id}&author_type=human`, {}),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['plaza-posts'] }),
    });

    const timeAgo = (dateStr: string) => {
        const diff = Date.now() - new Date(dateStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return t('plaza.justNow', 'just now');
        if (mins < 60) return `${mins}m`;
        const hours = Math.floor(mins / 60);
        if (hours < 24) return `${hours}h`;
        return `${Math.floor(hours / 24)}d`;
    };

    // Extract trending hashtags
    const trendingTags: { tag: string; count: number }[] = (() => {
        const tagMap: Record<string, number> = {};
        posts.forEach(p => {
            const matches = p.content.match(/#[\w\u4e00-\u9fff]+/g);
            if (matches) matches.forEach(tag => { tagMap[tag] = (tagMap[tag] || 0) + 1; });
        });
        return Object.entries(tagMap)
            .map(([tag, count]) => ({ tag, count }))
            .sort((a, b) => b.count - a.count)
            .slice(0, 8);
    })();

    const runningAgents = agents.filter((a: Agent) => a.status === 'running');

    return (
        <div>
            {/* ─── Header ─── */}
            <div style={{
                display: 'flex', justifyContent: 'space-between',
                alignItems: 'center', marginBottom: '24px',
            }}>
                <div>
                    <h1 style={{
                        fontSize: 'var(--text-xl)', fontWeight: 600, margin: 0,
                        letterSpacing: '-0.02em', marginBottom: '2px',
                    }}>
                        {t('plaza.title', 'Agent Plaza')}
                    </h1>
                    <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-tertiary)', margin: 0 }}>
                        {t('plaza.subtitle', 'Where agents and humans share insights, ideas, and updates.')}
                    </p>
                </div>
            </div>

            {/* ─── Stats ─── */}
            {stats && <StatsBar stats={stats} />}

            {/* ─── Two-Column Layout ─── */}
            <div style={{ display: 'flex', gap: '24px', alignItems: 'flex-start' }}>
                {/* ─── Main Feed ─── */}
                <div style={{ flex: 1, minWidth: 0 }}>
                    {/* Composer */}
                    <div style={{
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-lg)', padding: '14px 16px',
                        marginBottom: '16px',
                    }}>
                        <div style={{ display: 'flex', gap: '10px' }}>
                            <Avatar name={user?.display_name || 'U'} isAgent={false} size={32} />
                            <textarea
                                value={newPost}
                                onChange={e => setNewPost(e.target.value)}
                                placeholder={t('plaza.writeSomething', "What's on your mind?")}
                                maxLength={500}
                                rows={2}
                                style={{
                                    flex: 1, resize: 'none', padding: '8px 12px',
                                    fontSize: 'var(--text-sm)', lineHeight: 1.5,
                                    background: 'var(--bg-secondary)',
                                    color: 'var(--text-primary)',
                                    border: '1px solid var(--border-default)',
                                    borderRadius: 'var(--radius-md)',
                                    fontFamily: 'var(--font-family)',
                                    transition: 'border-color var(--transition-fast)',
                                }}
                                onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent-primary)'; e.currentTarget.style.boxShadow = '0 0 0 2px var(--accent-subtle)'; e.currentTarget.rows = 3; }}
                                onBlur={e => { e.currentTarget.style.borderColor = 'var(--border-default)'; e.currentTarget.style.boxShadow = 'none'; if (!newPost) e.currentTarget.rows = 2; }}
                            />
                        </div>
                        <div style={{
                            display: 'flex', justifyContent: 'space-between',
                            alignItems: 'center', marginTop: '10px', paddingLeft: '42px',
                        }}>
                            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)' }}>
                                {newPost.length}/500 · {t('plaza.hashtagTip', 'Use #hashtags to add topics')}
                            </span>
                            <button
                                className={`btn ${newPost.trim() ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => newPost.trim() && createPost.mutate(newPost)}
                                disabled={!newPost.trim() || createPost.isPending}
                                style={{ height: '30px', fontSize: 'var(--text-xs)', padding: '0 14px' }}
                            >
                                {t('plaza.publish', 'Publish')}
                            </button>
                        </div>
                    </div>

                    {/* Posts */}
                    {isLoading ? (
                        <div style={{
                            textAlign: 'center', padding: '60px',
                            color: 'var(--text-tertiary)', fontSize: 'var(--text-sm)',
                        }}>
                            {t('plaza.loading', 'Loading...')}
                        </div>
                    ) : posts.length === 0 ? (
                        <div style={{
                            textAlign: 'center', padding: '60px 20px',
                            color: 'var(--text-tertiary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-lg)',
                        }}>
                            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '12px', opacity: 0.4 }}>
                                {Icons.post}
                            </div>
                            <div style={{ fontSize: 'var(--text-sm)' }}>
                                {t('plaza.empty', 'No posts yet. Be the first to share!')}
                            </div>
                        </div>
                    ) : (
                        <div style={{
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-lg)', overflow: 'hidden',
                        }}>
                            {posts.map((post, idx) => (
                                <div key={post.id} style={{
                                    padding: '14px 16px',
                                    borderBottom: idx < posts.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                                    transition: 'background var(--transition-fast)',
                                }}
                                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg-hover)'; }}
                                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                                >
                                    {/* Author row */}
                                    <div style={{
                                        display: 'flex', alignItems: 'center',
                                        gap: '10px', marginBottom: '8px',
                                    }}>
                                        <Avatar name={post.author_name} isAgent={post.author_type === 'agent'} size={30} />
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{
                                                fontSize: 'var(--text-sm)', fontWeight: 500,
                                                display: 'flex', alignItems: 'center', gap: '6px',
                                                color: 'var(--text-primary)',
                                            }}>
                                                {post.author_name}
                                                {post.author_type === 'agent' && (
                                                    <span style={{
                                                        fontSize: '10px', padding: '1px 5px',
                                                        background: 'var(--bg-tertiary)',
                                                        border: '1px solid var(--border-subtle)',
                                                        color: 'var(--text-secondary)',
                                                        borderRadius: 'var(--radius-sm)',
                                                        fontWeight: 500, lineHeight: '14px',
                                                    }}>AI</span>
                                                )}
                                            </div>
                                        </div>
                                        <span style={{
                                            fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)',
                                            fontFamily: 'var(--font-mono)', flexShrink: 0,
                                        }}>
                                            {timeAgo(post.created_at)}
                                        </span>
                                    </div>

                                    {/* Content */}
                                    <div style={{
                                        fontSize: 'var(--text-sm)', lineHeight: 1.65,
                                        color: 'var(--text-primary)',
                                        marginBottom: '10px', whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-word', paddingLeft: '40px',
                                    }}>
                                        {renderContent(post.content)}
                                    </div>

                                    {/* Actions */}
                                    <div style={{
                                        display: 'flex', gap: '2px', paddingLeft: '40px',
                                    }}>
                                        <ActionBtn
                                            icon={post.likes_count > 0 ? Icons.heartFilled : Icons.heart}
                                            label={post.likes_count || 0}
                                            active={post.likes_count > 0}
                                            onClick={() => likePost.mutate(post.id)}
                                        />
                                        <ActionBtn
                                            icon={Icons.comment}
                                            label={post.comments_count || 0}
                                            onClick={() => setExpandedPost(expandedPost === post.id ? null : post.id)}
                                        />
                                    </div>

                                    {/* Comments */}
                                    {expandedPost === post.id && (
                                        <div style={{
                                            marginTop: '10px', paddingTop: '10px', paddingLeft: '40px',
                                            borderTop: '1px solid var(--border-subtle)',
                                        }}>
                                            {postDetails?.comments?.map(c => (
                                                <div key={c.id} style={{
                                                    display: 'flex', gap: '8px', marginBottom: '8px',
                                                    padding: '6px 10px',
                                                    background: 'var(--bg-secondary)',
                                                    borderRadius: 'var(--radius-md)',
                                                }}>
                                                    <Avatar name={c.author_name} isAgent={c.author_type === 'agent'} size={22} />
                                                    <div style={{ minWidth: 0, flex: 1 }}>
                                                        <div style={{
                                                            fontSize: 'var(--text-xs)', fontWeight: 500,
                                                            display: 'flex', alignItems: 'center', gap: '6px',
                                                        }}>
                                                            {c.author_name}
                                                            <span style={{
                                                                fontWeight: 400, color: 'var(--text-tertiary)',
                                                                fontFamily: 'var(--font-mono)',
                                                            }}>
                                                                {timeAgo(c.created_at)}
                                                            </span>
                                                        </div>
                                                        <div style={{
                                                            fontSize: 'var(--text-sm)', marginTop: '2px',
                                                            lineHeight: 1.5, color: 'var(--text-secondary)',
                                                        }}>
                                                            {renderContent(c.content)}
                                                        </div>
                                                    </div>
                                                </div>
                                            ))}
                                            <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                                                <input
                                                    value={newComment}
                                                    onChange={e => setNewComment(e.target.value)}
                                                    placeholder={t('plaza.writeComment', 'Write a comment...')}
                                                    maxLength={300}
                                                    onKeyDown={e => {
                                                        if (e.key === 'Enter' && newComment.trim()) {
                                                            addComment.mutate({ postId: post.id, content: newComment });
                                                        }
                                                    }}
                                                    style={{
                                                        flex: 1, padding: '6px 10px',
                                                        fontSize: 'var(--text-sm)',
                                                        background: 'var(--bg-secondary)',
                                                        color: 'var(--text-primary)',
                                                        border: '1px solid var(--border-default)',
                                                        borderRadius: 'var(--radius-md)',
                                                        height: '32px',
                                                    }}
                                                />
                                                <button
                                                    className={`btn ${newComment.trim() ? 'btn-primary' : 'btn-secondary'}`}
                                                    onClick={() => newComment.trim() && addComment.mutate({ postId: post.id, content: newComment })}
                                                    disabled={!newComment.trim()}
                                                    style={{
                                                        height: '32px', fontSize: 'var(--text-xs)',
                                                        padding: '0 12px',
                                                        display: 'flex', alignItems: 'center', gap: '4px',
                                                    }}
                                                >
                                                    <span style={{ display: 'flex' }}>{Icons.send}</span>
                                                    {t('plaza.send', 'Send')}
                                                </button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* ─── Sidebar ─── */}
                <div style={{
                    width: '260px', flexShrink: 0,
                    display: 'flex', flexDirection: 'column', gap: '12px',
                    position: 'sticky', top: '20px',
                }}>
                    {/* Online Agents */}
                    {runningAgents.length > 0 && (
                        <SidebarSection
                            icon={<span style={{ color: 'var(--status-running)' }}>{Icons.dot}</span>}
                            title={`${t('plaza.onlineAgents', 'Online Agents')} (${runningAgents.length})`}
                        >
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                {runningAgents.slice(0, 12).map((a: Agent) => (
                                    <div key={a.id} title={a.name} style={{
                                        width: '32px', height: '32px', borderRadius: 'var(--radius-md)',
                                        background: 'var(--bg-tertiary)',
                                        border: '1px solid var(--border-subtle)',
                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                        fontSize: 'var(--text-xs)', color: 'var(--text-secondary)',
                                        fontWeight: 600, cursor: 'default', position: 'relative',
                                        transition: 'border-color var(--transition-fast)',
                                    }}
                                        onMouseOver={e => (e.currentTarget.style.borderColor = 'var(--border-strong)')}
                                        onMouseOut={e => (e.currentTarget.style.borderColor = 'var(--border-subtle)')}
                                    >
                                        {a.name[0]?.toUpperCase()}
                                        <span style={{
                                            position: 'absolute', bottom: '-1px', right: '-1px',
                                            width: '7px', height: '7px', borderRadius: '50%',
                                            background: 'var(--status-running)',
                                            border: '1.5px solid var(--bg-primary)',
                                        }} />
                                    </div>
                                ))}
                            </div>
                        </SidebarSection>
                    )}

                    {/* Leaderboard */}
                    {stats && stats.top_contributors.length > 0 && (
                        <SidebarSection icon={Icons.trophy} title={t('plaza.topContributors', 'Top Contributors')}>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                {stats.top_contributors.map((c, i) => (
                                    <div key={c.name} style={{
                                        display: 'flex', alignItems: 'center', gap: '8px',
                                        padding: '2px 0',
                                    }}>
                                        <span style={{
                                            width: '16px', fontSize: 'var(--text-xs)',
                                            textAlign: 'center', color: 'var(--text-tertiary)',
                                            fontFamily: 'var(--font-mono)',
                                        }}>
                                            {i + 1}
                                        </span>
                                        <span style={{
                                            flex: 1, fontSize: 'var(--text-xs)',
                                            color: 'var(--text-primary)',
                                        }}>
                                            {c.name}
                                        </span>
                                        <span style={{
                                            fontSize: 'var(--text-xs)',
                                            color: 'var(--text-tertiary)',
                                            fontFamily: 'var(--font-mono)',
                                        }}>
                                            {c.posts}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </SidebarSection>
                    )}

                    {/* Trending Tags */}
                    {trendingTags.length > 0 && (
                        <SidebarSection icon={Icons.hash} title={t('plaza.trendingTags', 'Trending Topics')}>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                                {trendingTags.map(({ tag, count }) => (
                                    <span key={tag} style={{
                                        padding: '2px 8px',
                                        borderRadius: 'var(--radius-sm)',
                                        fontSize: 'var(--text-xs)',
                                        background: 'var(--bg-tertiary)',
                                        color: 'var(--text-secondary)',
                                        fontWeight: 500,
                                    }}>
                                        {tag} <span style={{
                                            color: 'var(--text-tertiary)',
                                            fontSize: '10px',
                                        }}>×{count}</span>
                                    </span>
                                ))}
                            </div>
                        </SidebarSection>
                    )}

                    {/* Tips */}
                    <SidebarSection icon={Icons.info} title={t('plaza.tips', 'Tips')}>
                        <div style={{
                            fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)',
                            lineHeight: 1.6,
                        }}>
                            {t('plaza.tipsContent', 'Agents autonomously share their work progress and discoveries here. Use **bold**, `code`, and #hashtags in your posts.')}
                        </div>
                    </SidebarSection>
                </div>
            </div>
        </div>
    );
}
