/** Global state management with Zustand */

import { create } from 'zustand';
import type { User, Agent } from '../types';

interface AuthStore {
    user: User | null;
    token: string | null;
    setAuth: (user: User, token: string) => void;
    setUser: (user: User) => void;
    logout: () => void;
    isAuthenticated: () => boolean;
}

export const useAuthStore = create<AuthStore>((set, get) => ({
    user: null,
    token: localStorage.getItem('token'),

    setAuth: (user, token) => {
        localStorage.setItem('token', token);
        set({ user, token });
    },

    setUser: (user) => {
        set({ user });
    },

    logout: () => {
        localStorage.removeItem('token');
        set({ user: null, token: null });
    },

    isAuthenticated: () => !!get().token,
}));

interface AppStore {
    sidebarCollapsed: boolean;
    toggleSidebar: () => void;
    selectedAgentId: string | null;
    setSelectedAgent: (id: string | null) => void;
}

export const useAppStore = create<AppStore>((set) => ({
    sidebarCollapsed: false,
    toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    selectedAgentId: null,
    setSelectedAgent: (id) => set({ selectedAgentId: id }),
}));
