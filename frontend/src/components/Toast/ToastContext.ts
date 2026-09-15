import { createContext, useContext } from "react";

export type ToastType = "info" | "success" | "warning" | "error";

export interface ToastOptions {
  duration?: number;
  details?: string;
}

export interface ToastContextValue {
  show: (type: ToastType, message: string, options?: ToastOptions) => void;
  info: (message: string, options?: ToastOptions) => void;
  success: (message: string, options?: ToastOptions) => void;
  warning: (message: string, options?: ToastOptions) => void;
  error: (message: string, options?: ToastOptions) => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within ToastProvider");
  return context;
}
