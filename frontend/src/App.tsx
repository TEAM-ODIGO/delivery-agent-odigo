import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ToastContext } from "./lib/hooks";
import NewOrder from "./pages/NewOrder";
import RunPage from "./pages/RunPage";
import MyRuns from "./pages/MyRuns";
import Admin from "./pages/Admin";
import Owner from "./pages/Owner";
import Tap from "./pages/Tap";
import Settings from "./pages/Settings";
import Splash from "./components/Splash";
import { preloadImagesWhenIdle } from "./lib/images";

export default function App() {
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const show = useCallback((msg: string) => {
    setToast(msg);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 3200);
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  // 후보·결제수단 화면의 음식·카드 이미지를 미리 받아 둔다 (src/lib/images.ts)
  useEffect(() => {
    preloadImagesWhenIdle();
  }, []);

  return (
    <ToastContext.Provider value={show}>
      <Routes>
        <Route path="/admin/*" element={<Admin />} />
        <Route path="/owner/*" element={<Owner />} />
        <Route
          path="*"
          element={
            <div className="app-shell">
              {/* 첫 접속 스플래시 (이슈 #96): 첫 화면으로 들어올 때만. /tap 등 링크로 바로 여는 화면은 제외 */}
              <Routes>
                <Route path="/" element={<Splash />} />
                <Route path="*" element={null} />
              </Routes>
              <Routes>
                <Route path="/" element={<NewOrder />} />
                <Route path="/runs/:requestId" element={<RunPage />} />
                <Route path="/orders" element={<MyRuns />} />
                <Route path="/tap" element={<Tap />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </div>
          }
        />
      </Routes>
      {toast && (
        <div className="toast" role="alert">
          {toast}
        </div>
      )}
    </ToastContext.Provider>
  );
}
