import { lazy, Suspense, useCallback, useState } from "react";
import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Splash from "./components/Splash";
import { Spinner, ToastProvider } from "./components/ui";
import ProjectsPage from "./pages/ProjectsPage";

// Heavy pages (PDF / DOCX / syntax-highlight renderers, settings form) load on demand.
const ProjectWorkspace = lazy(() => import("./pages/ProjectWorkspace"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

function Fallback() {
  return <div className="flex justify-center py-24 text-slate-400"><Spinner className="h-6 w-6" /></div>;
}

export default function App() {
  const [splash, setSplash] = useState(true);
  const hideSplash = useCallback(() => setSplash(false), []);

  return (
    <ToastProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<ProjectsPage />} />
          <Route path="projects/:projectId" element={<Suspense fallback={<Fallback />}><ProjectWorkspace /></Suspense>} />
          <Route path="settings" element={<Suspense fallback={<Fallback />}><SettingsPage /></Suspense>} />
        </Route>
      </Routes>
      {splash && <Splash onDone={hideSplash} />}
    </ToastProvider>
  );
}
