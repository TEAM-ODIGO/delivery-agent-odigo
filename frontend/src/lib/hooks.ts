import { createContext, useContext, useEffect, useState } from "react";

// 1초마다 바뀌는 현재 시각 (유효시간 카운트다운용)
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export const ToastContext = createContext<(msg: string) => void>(() => {});
export const useToast = () => useContext(ToastContext);
