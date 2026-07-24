export const WORKSPACE_TEXT_UPLOAD_EXTENSIONS = [
    '.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml', '.js', '.ts', '.py',
    '.html', '.css', '.sh', '.log', '.gitkeep', '.env',
] as const;

export const WORKSPACE_BINARY_UPLOAD_EXTENSIONS = [
    '.pdf', '.docx', '.xlsx', '.pptx',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp',
] as const;

export const WORKSPACE_UPLOAD_EXTENSIONS = [
    ...WORKSPACE_TEXT_UPLOAD_EXTENSIONS,
    ...WORKSPACE_BINARY_UPLOAD_EXTENSIONS,
] as const;

export const WORKSPACE_UPLOAD_ACCEPT = WORKSPACE_UPLOAD_EXTENSIONS.join(',');
