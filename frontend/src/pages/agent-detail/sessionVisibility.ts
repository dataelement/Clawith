interface SessionVisibilityInput {
    user_id?: string | number | null;
    source_channel?: string | null;
    participant_type?: string | null;
    is_group?: boolean | null;
}

export function belongsInOtherSessions(
    session: SessionVisibilityInput,
    viewerUserId: string,
): boolean {
    const sourceChannel = String(session.source_channel || '').toLowerCase();
    const participantType = String(session.participant_type || '').toLowerCase();

    if (session.is_group || participantType === 'group') return true;
    if (sourceChannel === 'agent' || participantType === 'agent') return true;

    const sessionUserId = session.user_id == null ? '' : String(session.user_id);
    return !viewerUserId || sessionUserId !== viewerUserId;
}
