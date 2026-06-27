import { Routes, Route, Navigate } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import Layout from "./components/Layout";
import RunPage from "./pages/RunPage";
import TraceListPage from "./pages/TraceListPage";
import TraceDetailPage from "./pages/TraceDetailPage";
import ToolsPage from "./pages/ToolsPage";
import ComparePage from "./pages/ComparePage";
import EvalPage from "./pages/EvalPage";

function App() {
  return (
    <MotionConfig reducedMotion="user">
      <Layout>
        <Routes>
          <Route path="/" element={<Navigate to="/run" replace />} />
          <Route path="/run" element={<RunPage />} />
          <Route path="/traces" element={<TraceListPage />} />
          <Route path="/traces/:id" element={<TraceDetailPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/eval" element={<EvalPage />} />
        </Routes>
      </Layout>
    </MotionConfig>
  );
}

export default App;
