// 당겨서 새로고침 (이슈 #68): 페이지 맨 위에서 목록을 아래로 당겼다 놓으면 onRefresh.
// 휴대폰(터치)과 노트북(마우스 끌기) 모두 된다. 당긴 뒤 놓을 때 생기는 클릭은 막아서 카드가 선택되지 않게 한다.
import { useEffect, useRef, useState } from "react";

export const PULL_READY = 64; // 이만큼 당기면 놓았을 때 새로고침
const PULL_MAX = 96;

export function usePullToRefresh<T extends HTMLElement>(onRefresh: () => void, enabled: boolean) {
  const ref = useRef<T>(null);
  const [pull, setPull] = useState(0);
  const pullRef = useRef(0);
  const startY = useRef<number | null>(null);
  const dragged = useRef(false);
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;

  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    const set = (v: number) => {
      pullRef.current = v;
      setPull(v);
    };
    const atTop = () => (document.scrollingElement?.scrollTop ?? window.scrollY) <= 0;
    const begin = (y: number) => {
      dragged.current = false;
      startY.current = atTop() ? y : null;
    };
    const move = (y: number, e: Event) => {
      if (startY.current === null) return;
      const dy = y - startY.current;
      if (dy <= 0) return set(0);
      if (dy > 8) dragged.current = true;
      if (e.cancelable) e.preventDefault(); // 브라우저 자체 새로고침·스크롤 대신 우리가 처리
      set(Math.min(dy * 0.5, PULL_MAX));
    };
    const end = () => {
      if (startY.current === null) return;
      startY.current = null;
      if (pullRef.current >= PULL_READY) refresh.current();
      set(0);
    };

    const onTouchStart = (e: TouchEvent) => begin(e.touches[0].clientY);
    const onTouchMove = (e: TouchEvent) => move(e.touches[0].clientY, e);
    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      begin(e.clientY);
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp, { once: true });
    };
    const onMouseMove = (e: MouseEvent) => move(e.clientY, e);
    const onMouseUp = () => {
      window.removeEventListener("mousemove", onMouseMove);
      end();
    };
    const onClick = (e: MouseEvent) => {
      if (!dragged.current) return;
      dragged.current = false;
      e.preventDefault();
      e.stopPropagation();
    };

    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", end);
    el.addEventListener("touchcancel", end);
    el.addEventListener("mousedown", onMouseDown);
    el.addEventListener("click", onClick, true);
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", end);
      el.removeEventListener("touchcancel", end);
      el.removeEventListener("mousedown", onMouseDown);
      el.removeEventListener("click", onClick, true);
      window.removeEventListener("mousemove", onMouseMove);
    };
  }, [enabled]);

  return { ref, pull, ready: pull >= PULL_READY };
}
