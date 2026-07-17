import { MotionConfig } from "framer-motion";
import { Navigate, Route, Routes } from "react-router-dom";
import Phase1Layout from "./components/Phase1Layout";
import ExperimentDetailPage from "./pages/ExperimentDetailPage";
import ExperimentsPage from "./pages/ExperimentsPage";
import NewExperimentPage from "./pages/NewExperimentPage";
import RunDetailPage from "./pages/RunDetailPage";

export default function Phase1App() {
  return (
    <MotionConfig reducedMotion="user">
      <Phase1Layout>
        <Routes>
          <Route path="/" element={<Navigate to="/experiments" replace />} />
          <Route path="/experiments" element={<ExperimentsPage />} />
          <Route path="/experiments/new" element={<NewExperimentPage />} />
          <Route path="/experiments/:id" element={<ExperimentDetailPage />} />
          <Route path="/runs/:id" element={<RunDetailPage />} />
          <Route path="*" element={<Navigate to="/experiments" replace />} />
        </Routes>
      </Phase1Layout>
    </MotionConfig>
  );
}
