import { useCallback, useState } from "react";
import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Splash from "./components/Splash";
import ProjectsPage from "./pages/ProjectsPage";
import ProjectWorkspace from "./pages/ProjectWorkspace";
import SettingsPage from "./pages/SettingsPage";

export default function App() {
  const [splash, setSplash] = useState(true);
  const hideSplash = useCallback(() => setSplash(false), []);

  return (
    <>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<ProjectsPage />} />
          <Route path="projects/:projectId" element={<ProjectWorkspace />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
      {splash && <Splash onDone={hideSplash} />}
    </>
  );
}
