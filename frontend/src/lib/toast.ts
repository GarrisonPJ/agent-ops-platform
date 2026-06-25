type ToastVariant = "success" | "error" | "info";

export interface ToastData {
  id: string;
  message: string;
  variant: ToastVariant;
}

type ToastListener = (toast: ToastData) => void;

let listeners: ToastListener[] = [];
let counter = 0;

export function subscribe(listener: ToastListener) {
  listeners.push(listener);
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

function emit(message: string, variant: ToastVariant) {
  const id = String(++counter);
  listeners.forEach((l) => l({ id, message, variant }));
  return id;
}

export const toast = {
  success: (msg: string) => emit(msg, "success"),
  error: (msg: string) => emit(msg, "error"),
  info: (msg: string) => emit(msg, "info"),
};
