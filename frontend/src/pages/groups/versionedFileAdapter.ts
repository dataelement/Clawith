interface VersionedTextFile {
    content: string;
    version_token: string | null;
}

interface VersionedFileOperations {
    read: (path: string) => Promise<VersionedTextFile>;
    write: (
        path: string,
        content: string,
        expectedVersionToken: string | null,
        requireAbsent: boolean,
    ) => Promise<VersionedTextFile>;
    delete: (path: string, expectedVersionToken: string | null) => Promise<unknown>;
}

/**
 * Keep the backend version captured by list/read and use that exact value for the next mutation.
 * A save must never refresh the token first: doing so would silently turn stale content into an
 * unconditional overwrite of somebody else's newer edit.
 */
export function createVersionedFileAdapter(operations: VersionedFileOperations) {
    const versions = new Map<string, string | null>();

    const remember = (path: string, versionToken: string | null) => {
        versions.set(path, versionToken);
    };

    return {
        remember,
        snapshot(path: string) {
            return versions.has(path)
                ? { known: true, versionToken: versions.get(path) ?? null }
                : { known: false, versionToken: null };
        },
        async read(path: string) {
            const file = await operations.read(path);
            remember(path, file.version_token);
            return { content: file.content };
        },
        async write(path: string, content: string) {
            const known = versions.has(path);
            const file = await operations.write(
                path,
                content,
                versions.get(path) ?? null,
                !known,
            );
            remember(path, file.version_token);
            return file;
        },
        async delete(path: string) {
            const result = await operations.delete(path, versions.get(path) ?? null);
            versions.delete(path);
            return result;
        },
    };
}
