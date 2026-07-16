export const onboardingKickoffKey = (agentId: string, userId: string): string =>
    `${agentId}:${userId}`;

export const shouldKickoffOnboarding = ({
    websocketReady,
    messagesLoaded,
    runtimeStateLoaded,
    messageCount,
    hasActiveRun,
}: {
    websocketReady: boolean;
    messagesLoaded: boolean;
    runtimeStateLoaded: boolean;
    messageCount: number;
    hasActiveRun: boolean;
}): boolean => (
    websocketReady
    && messagesLoaded
    && runtimeStateLoaded
    && messageCount === 0
    && !hasActiveRun
);
