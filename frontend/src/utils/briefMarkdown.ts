export type BriefDefaultSectionId = 'goal' | 'context' | 'constraints';

export interface BriefSection {
    id: string;
    heading: string;
    body: string;
    isDefault: boolean;
    defaultId?: BriefDefaultSectionId;
}

export interface ParsedBrief {
    title: string;
    sections: BriefSection[];
    unparsable: boolean;
}

const DEFAULT_SECTIONS: { id: BriefDefaultSectionId; pattern: RegExp; canonical: string }[] = [
    { id: 'goal', pattern: /^目标(\s*\/\s*Goal)?$/i, canonical: '目标 / Goal' },
    { id: 'context', pattern: /^背景(\s*\/\s*Context)?$/i, canonical: '背景 / Context' },
    { id: 'constraints', pattern: /^限制条件(\s*\/\s*Constraints)?$/i, canonical: '限制条件 / Constraints' },
];

const DEFAULT_ORDER: BriefDefaultSectionId[] = ['goal', 'context', 'constraints'];

function matchDefaultId(heading: string): BriefDefaultSectionId | null {
    const trimmed = heading.trim();
    for (const def of DEFAULT_SECTIONS) {
        if (def.pattern.test(trimmed)) return def.id;
    }
    return null;
}

function getCanonical(id: BriefDefaultSectionId): string {
    return DEFAULT_SECTIONS.find(d => d.id === id)!.canonical;
}

function isPlaceholderLine(line: string): boolean {
    const trimmed = line.trim();
    if (!trimmed) return true;
    if (trimmed.startsWith('>')) return true;
    if (/^_[^_].*[^_]_$|^_[^_]_$/.test(trimmed)) return true;
    if (/^\*[^*].*[^*]\*$|^\*[^*]\*$/.test(trimmed)) return true;
    return false;
}

function isPlaceholderBody(body: string): boolean {
    const stripped = body.replace(/<!--[\s\S]*?-->/g, '');
    const lines = stripped.split('\n');
    if (lines.every(l => !l.trim())) return true;
    return lines.every(isPlaceholderLine);
}

export function parseBrief(md: string): ParsedBrief {
    const lines = md.split('\n');
    let title = '';
    let i = 0;

    while (i < lines.length && !lines[i].trim()) i++;

    if (i < lines.length && lines[i].startsWith('# ')) {
        title = lines[i].slice(2).trim();
        i++;
    }

    while (i < lines.length && !lines[i].startsWith('## ')) {
        i++;
    }

    const parsed: BriefSection[] = [];
    let currentHeading = '';
    let currentBody: string[] = [];
    let customCounter = 0;

    const flush = () => {
        if (!currentHeading) return;
        const bodyText = currentBody.join('\n').replace(/^\n+|\n+$/g, '');
        const cleanBody = isPlaceholderBody(bodyText) ? '' : bodyText;
        const matched = matchDefaultId(currentHeading);
        if (matched) {
            parsed.push({
                id: matched,
                heading: getCanonical(matched),
                body: cleanBody,
                isDefault: true,
                defaultId: matched,
            });
        } else {
            parsed.push({
                id: `custom-${customCounter++}`,
                heading: currentHeading,
                body: cleanBody,
                isDefault: false,
            });
        }
    };

    while (i < lines.length) {
        const line = lines[i];
        if (line.startsWith('## ')) {
            flush();
            currentHeading = line.slice(3).trim();
            currentBody = [];
        } else {
            currentBody.push(line);
        }
        i++;
    }
    flush();

    const unparsable = parsed.length === 0 && md.trim().length > 0;

    if (unparsable) {
        return { title, sections: [], unparsable: true };
    }

    const existingDefaultIds = new Set(parsed.filter(s => s.isDefault).map(s => s.defaultId!));
    const missingDefaults: BriefSection[] = DEFAULT_SECTIONS
        .filter(d => !existingDefaultIds.has(d.id))
        .map(d => ({
            id: d.id,
            heading: d.canonical,
            body: '',
            isDefault: true,
            defaultId: d.id,
        }));

    const allDefaults = [
        ...parsed.filter(s => s.isDefault),
        ...missingDefaults,
    ].sort((a, b) => DEFAULT_ORDER.indexOf(a.defaultId!) - DEFAULT_ORDER.indexOf(b.defaultId!));

    const customs = parsed.filter(s => !s.isDefault);

    return {
        title,
        sections: [...allDefaults, ...customs],
        unparsable: false,
    };
}

export function serializeBrief(title: string, sections: BriefSection[]): string {
    const parts: string[] = [];
    const trimmedTitle = title.trim();
    if (trimmedTitle) {
        parts.push(`# ${trimmedTitle}`);
        parts.push('');
    }
    for (const section of sections) {
        const heading = section.heading.trim();
        if (!heading) continue;
        parts.push(`## ${heading}`);
        parts.push('');
        const body = section.body.trim();
        if (body) {
            parts.push(body);
            parts.push('');
        }
    }
    return parts.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
}

export function isFormFriendly(md: string): boolean {
    return !parseBrief(md).unparsable;
}
