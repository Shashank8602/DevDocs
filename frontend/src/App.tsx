import { lazy, ReactNode, Suspense, useCallback, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import Splash from "./components/Splash";
import { EmptyState, Spinner, ToastProvider } from "./components/ui";
import LoginPage from "./pages/LoginPage";
import ProjectsPage from "./pages/ProjectsPage";

// Heavy pages (PDF / DOCX / syntax-highlight renderers, settings form) load on demand.
const ProjectWorkspace = lazy(() => import("./pages/ProjectWorkspace"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

const SPLASH_KEY = "devdocs-splash-shown";

function Fallback() {
  return <div className="flex justify-center py-24 text-slate-400"><Spinner className="h-6 w-6" /></div>;
}

/** The same screen for a page that does not exist and one the person may not see. */
function NotFound() {
  return (
    <EmptyState icon="alert" title="Page not found" action={<Link to="/" className="btn-secondary">Back to projects</Link>}>
      This page does not exist.
    </EmptyState>
  );
}

function AdminOnly({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  return isAdmin ? <Suspense fallback={<Fallback />}>{children}</Suspense> : <NotFound />;
}

function Gate() {
  const { phase, retry } = useAuth();
  if (phase === "loading") return <div className="flex h-screen items-center justify-center text-slate-400"><Spinner className="h-6 w-6" /></div>;
  if (phase === "offline") {
    return (
      <div className="flex h-screen items-center justify-center p-4">
        <EmptyState icon="alert" title="Can't reach the server" action={<button onClick={retry} className="btn-primary">Try again</button>}>
          Check that the backend is running, then try again.
        </EmptyState>
      </div>
    );
  }
  if (phase === "setup" || phase === "signed-out") return <LoginPage firstRun={phase === "setup"} />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ProjectsPage />} />
        <Route path="projects/:projectId" element={<Suspense fallback={<Fallback />}><ProjectWorkspace /></Suspense>} />
        <Route path="settings" element={<AdminOnly><SettingsPage /></AdminOnly>} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  // The logo splash plays once per browser session, not on every reload.
  const [splash, setSplash] = useState(() => {
    try { return !sessionStorage.getItem(SPLASH_KEY); } catch { return false; }
  });
  const hideSplash = useCallback(() => {
    setSplash(false);
    try { sessionStorage.setItem(SPLASH_KEY, "1"); } catch { /* storage unavailable: fine */ }
  }, []);

  return (
    <ToastProvider>
      <Gate />
      {splash && <Splash onDone={hideSplash} />}
    </ToastProvider>
  );
}
