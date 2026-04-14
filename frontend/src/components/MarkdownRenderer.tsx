/**
 * Lightweight Markdown renderer — no external dependencies.
 * Renders: headings, bold, italic, inline code, code blocks,
 * unordered/ordered lists, blockquotes, horizontal rules, links, tables.
 */
import React, { useMemo } from 'react';

function escapeHtml(str: string): string {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function renderInline(text: string): string {
    return text
        // Bold + italic
        .replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>')
        // Bold
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/__(.*?)__/g, '<strong>$1</strong>')
        // Italic
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/_(.*?)_/g, '<em>$1</em>')
        // Inline code
        .replace(/`([^`]+)`/g, '<code style="background:var(--bg-secondary);padding:1px 4px;border-radius:3px;font-family:monospace;font-size:0.9em">$1</code>')
        // Images
        .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, url) => {
            let finalUrl = url;
            if (finalUrl.startsWith('/api/agents/')) {
                const token = localStorage.getItem('token');
                if (token && !finalUrl.includes('token=')) {
                    finalUrl += (finalUrl.includes('?') ? '&' : '?') + `token=${token}`;
                }
            }
            return `<a href="${finalUrl}" target="_blank"><img src="${finalUrl}" alt="${alt}" style="max-width:100%;max-height:400px;border-radius:4px;margin:8px 0;object-fit:contain;cursor:pointer" /></a>`;
        })
        // Links
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
            // Avoid matching images that snuck through or weird nested stuff
            if (match.startsWith('!')) return match;
            let finalUrl = url;
            if (finalUrl.startsWith('/api/agents/')) {
                const token = localStorage.getItem('token');
                if (token && !finalUrl.includes('token=')) {
                    finalUrl += (finalUrl.includes('?') ? '&' : '?') + `token=${token}`;
                }
            }
            return `<a href="${finalUrl}" target="_blank" rel="noopener noreferrer" style="color:var(--accent-primary)">${text}</a>`;
        })
        // Strikethrough
        .replace(/~~(.*?)~~/g, '<del>$1</del>');
}

function markdownToHtml(md: string): string {
    const lines = md.split('\n');
    let html = '';
    let inCodeBlock = false;
    let codeLang = '';
    let codeLines: string[] = [];
    let inList: 'ul' | 'ol' | null = null;
    let inBlockquote = false;
    let inTable = false;
    let tableHeader = false;

    const flushList = () => {
        if (inList) { html += inList === 'ul' ? '</ul>' : '</ol>'; inList = null; }
    };
    const flushBlockquote = () => {
        if (inBlockquote) { html += '</blockquote>'; inBlockquote = false; }
    };
    const flushTable = () => {
        if (inTable) { html += '</tbody></table>'; inTable = false; tableHeader = false; }
    };

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];

        // Code block
        if (line.startsWith('```')) {
            if (!inCodeBlock) {
                flushList(); flushBlockquote(); flushTable();
                inCodeBlock = true;
                codeLang = line.slice(3).trim();
                codeLines = [];
            } else {
                const codeContent = escapeHtml(codeLines.join('\n'));
                html += `<pre style="background:var(--bg-secondary);border-radius:8px;padding:12px 16px;overflow-x:auto;margin:8px 0"><code style="font-family:monospace;font-size:12px;line-height:1.5"${codeLang ? ` class="language-${codeLang}"` : ''}>${codeContent}</code></pre>`;
                inCodeBlock = false;
                codeLang = '';
                codeLines = [];
            }
            continue;
        }
        if (inCodeBlock) { codeLines.push(line); continue; }

        // Blank line
        if (line.trim() === '') {
            flushList(); flushBlockquote(); flushTable();
            html += '<br>';
            continue;
        }

        // Headings
        const hMatch = line.match(/^(#{1,6})\s+(.*)/);
        if (hMatch) {
            flushList(); flushBlockquote(); flushTable();
            const level = hMatch[1].length;
            const sizes = ['1.6em', '1.4em', '1.2em', '1.1em', '1em', '0.9em'];
            const margins = ['20px 0 8px', '16px 0 6px', '14px 0 5px', '12px 0 4px', '10px 0 4px', '8px 0 4px'];
            html += `<h${level} style="margin:${margins[level - 1]};font-size:${sizes[level - 1]};font-weight:600;line-height:1.3">${renderInline(hMatch[2])}</h${level}>`;
            continue;
        }

        // Horizontal rule
        if (/^[-*_]{3,}$/.test(line.trim())) {
            flushList(); flushBlockquote(); flushTable();
            html += '<hr style="border:none;border-top:1px solid var(--border-color);margin:12px 0">';
            continue;
        }

        // Blockquote
        if (line.startsWith('> ')) {
            flushList(); flushTable();
            if (!inBlockquote) {
                html += '<blockquote style="border-left:3px solid var(--accent-primary);margin:8px 0;padding:4px 12px;color:var(--text-secondary);background:var(--bg-secondary);border-radius:0 4px 4px 0">';
                inBlockquote = true;
            }
            html += `<div>${renderInline(line.slice(2))}</div>`;
            continue;
        } else if (inBlockquote) {
            flushBlockquote();
        }

        // Tables
        if (line.includes('|')) {
            flushList(); flushBlockquote();
            const cols = line.split('|').map(c => c.trim()).filter((_, i, a) => i > 0 && i < a.length - 1);
            // Separator row
            if (cols.every(c => /^[-:]+$/.test(c))) {
                tableHeader = true;
                continue;
            }
            if (!inTable) {
                html += '<table style="border-collapse:collapse;margin:8px 0;font-size:13px;width:100%"><thead>';
                inTable = true;
                tableHeader = false;
                // This is the header row
                html += '<tr>' + cols.map(c => `<th style="border:1px solid rgba(128,128,128,0.4);padding:6px 10px;background:var(--bg-secondary);text-align:left;font-weight:600">${renderInline(c)}</th>`).join('') + '</tr>';
                html += '</thead><tbody>';
            } else {
                html += '<tr>' + cols.map(c => `<td style="border:1px solid rgba(128,128,128,0.4);padding:6px 10px">${renderInline(c)}</td>`).join('') + '</tr>';
            }
            continue;
        } else if (inTable) {
            flushTable();
        }

        // Unordered list
        const ulMatch = line.match(/^(\s*)[*\-+]\s+(.*)/);
        if (ulMatch) {
            flushBlockquote(); flushTable();
            if (inList !== 'ul') { if (inList) flushList(); html += '<ul style="margin:6px 0;padding-left:24px">'; inList = 'ul'; }
            html += `<li style="margin:2px 0">${renderInline(ulMatch[2])}</li>`;
            continue;
        }

        // Ordered list
        const olMatch = line.match(/^(\s*)\d+\.\s+(.*)/);
        if (olMatch) {
            flushBlockquote(); flushTable();
            if (inList !== 'ol') { if (inList) flushList(); html += '<ol style="margin:6px 0;padding-left:24px">'; inList = 'ol'; }
            html += `<li style="margin:2px 0">${renderInline(olMatch[2])}</li>`;
            continue;
        }

        // Regular paragraph
        flushList(); flushBlockquote(); flushTable();
        html += `<p style="margin:4px 0;line-height:1.7">${renderInline(line)}</p>`;
    }

    // Close any open structures
    flushList(); flushBlockquote(); flushTable();
    if (inCodeBlock) {
        html += `<pre style="background:var(--bg-secondary);border-radius:8px;padding:12px 16px"><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`;
    }

    return html;
}

interface MarkdownRendererProps {
    content: string;
    style?: React.CSSProperties;
    className?: string;
}

export const MarkdownRenderer = React.memo(function MarkdownRenderer({ content, style, className }: MarkdownRendererProps) {
    const html = useMemo(() => markdownToHtml(content), [content]);
    return (
        <div
            className={className}
            style={{ lineHeight: 1.6, fontSize: 'inherit', ...style, wordBreak: 'break-word' }}
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
});

export default MarkdownRenderer;
