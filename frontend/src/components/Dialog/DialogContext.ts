import { createContext, useContext } from "react";

export type DialogType = "info" | "success" | "warning" | "error";

export interface AlertOptions {
  title?: string;
  type?: DialogType;
  details?: string;
  confirmLabel?: string;
}

export interface ConfirmOptions {
  title?: string;
  danger?: boolean;
  confirmLabel?: string;
  cancelLabel?: string;
}

export interface DialogContextValue {
  alert: (message: string, options?: AlertOptions) => Promise<void>;
  confirm: (message: string, options?: ConfirmOptions) => Promise<boolean>;
}

export const DialogContext = createContext<DialogContextValue | null>(null);

export function useDialog(): DialogContextValue {
  const context = useContext(DialogContext);
  if (!context) throw new Error("useDialog must be used within DialogProvider");
  return context;
}
