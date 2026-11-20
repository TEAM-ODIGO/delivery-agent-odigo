import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ToastContext } from "./lib/hooks";
import NewOrder from "./pages/NewOrder";
import RunPage from "./pages/RunPage";
import MyRuns from "./pages/MyRuns";
import Admin from "./pages/Admin";
import Tap from "./pages/Tap";

export default function App() {
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const show = useCallback((msg: string) => {
    setToast(msg);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 3200);
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <ToastContext.Provider value={show}>
      <Routes>
        <Route path="/admin" element={<Admin />} />
        <Route
          path="*"
          element={
            <div className="app-shell">
              <Routes>
                <Route path="/" element={<NewOrder />} />
                <Route path="/runs/:requestId" element={<RunPage />} />
                <Route path="/orders" element={<MyRuns />} />
                <Route path="/tap" element={<Tap />} />
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
