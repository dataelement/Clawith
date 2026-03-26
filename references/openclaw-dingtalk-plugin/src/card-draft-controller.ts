/**
 * Card draft controller for throttled AI Card streaming updates.
 *
 * Wraps {@link createDraftStreamLoop} with a phase-based state machine
 * (idle → reasoning → answer) that manages what content is sent to the
 * DingTalk AI Card during the reply lifecycle.
 *
 * Responsibilities (and non-responsibilities):
 * - DOES manage throttled card preview updates via streamAICard
 * - DOES enforce single-flight, latest-wins, phase-gated semantics
 * - Does NOT handle tool append, finalize, or markdown fallback —
 *   those stay in inbound-handler's deliver callback.
 */

import { formatContentForCard, streamAICard } from "./card-service";
import { createDraftStreamLoop } from "./draft-stream-loop";
import type { AICardInstance, Logger } from "./types";

export type CardDraftPhase = "idle" | "reasoning" | "answer";

export interface CardDraftController {
    updateAnswer: (text: string) => void;
    updateReasoning: (text: string) => void;
    /** Signal that a new assistant turn has started (e.g. after a tool call). */
    notifyNewAssistantTurn: () => void;
    flush: () => Promise<void>;
    waitForInFlight: () => Promise<void>;
    stop: () => void;
    isFailed: () => boolean;
    /** Last content sent to card (reasoning or answer). */
    getLastContent: () => string;
    /** Last content sent to card during answer phase only. */
    getLastAnswerContent: () => string;
}

export function createCardDraftController(params: {
    card: AICardInstance;
    throttleMs?: number;
    log?: Logger;
}): CardDraftController {
    let phase: CardDraftPhase = "idle";
    let failed = false;
    let stopped = false;
    let lastSentContent = "";
    let lastAnswerContent = "";
    let answerPrefix = "";
    let turnBoundaryPending = false;

    const loop = createDraftStreamLoop({
        throttleMs: params.throttleMs ?? 300,
        isStopped: () => stopped || failed,
        sendOrEditStreamMessage: async (content: string) => {
            try {
                await streamAICard(params.card, content, false, params.log);
                lastSentContent = content;
                if (phase === "answer") {
                    lastAnswerContent = content;
                }
            } catch (err: unknown) {
                failed = true;
                const message = err instanceof Error ? err.message : String(err);
                params.log?.warn?.(`[DingTalk][AICard] Stream failed: ${message}`);
            }
        },
    });

    return {
        updateReasoning: (text: string) => {
            if (stopped || failed || phase === "answer") { return; }
            phase = "reasoning";
            const formatted = formatContentForCard(text, "thinking");
            if (formatted) {
                loop.update(formatted);
            }
        },

        updateAnswer: (text: string) => {
            if (stopped || failed) { return; }
            if (phase !== "answer") {
                params.log?.debug?.(`[DingTalk][Draft] phase ${phase} → answer`);
                phase = "answer";
                if (turnBoundaryPending && lastAnswerContent) {
                    answerPrefix = lastAnswerContent + "\n\n";
                }
                turnBoundaryPending = false;
            }
            const trimmed = text?.trimStart();
            if (trimmed) {
                loop.update(answerPrefix + trimmed);
            }
        },

        notifyNewAssistantTurn: () => {
            if (stopped || failed) { return; }
            turnBoundaryPending = true;
            if (phase === "reasoning") {
                loop.resetPending();
            }
            phase = "idle";
        },

        flush: () => loop.flush(),
        waitForInFlight: () => loop.waitForInFlight(),

        stop: () => {
            stopped = true;
            loop.stop();
        },

        isFailed: () => failed,
        getLastContent: () => lastSentContent,
        getLastAnswerContent: () => lastAnswerContent,
    };
}
