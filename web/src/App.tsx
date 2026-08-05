import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { EngineDetail } from "./pages/EngineDetail";
import { JobDetail } from "./pages/JobDetail";
import { Jobs } from "./pages/Jobs";
import { ListingAssets } from "./pages/ListingAssets";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/jobs/:jobId" element={<JobDetail />} />
        {/* The bare Engines list is gone (Dashboard is now the registry
            view) -- redirect rather than leave old bookmarks/links dead. */}
        <Route path="/engines" element={<Navigate to="/" replace />} />
        <Route path="/engines/:engineId" element={<EngineDetail />} />
        {/* Renamed from "Listing Workspace" to "Listing Assets" -- redirect
            rather than leave an old bookmark/link dead. */}
        <Route path="/listing-workspace" element={<Navigate to="/listing-assets" replace />} />
        <Route path="/listing-assets" element={<ListingAssets />} />
      </Routes>
    </Layout>
  );
}
