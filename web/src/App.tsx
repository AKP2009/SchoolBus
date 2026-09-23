import { Navigate, Route, Routes } from 'react-router-dom';
import { ManagerFleet, ManagerPlaceholder } from '@/pages/manager';
import { OperatorMachine, OperatorPlaceholder, OperatorTasks } from '@/pages/operator';
import { Styleguide } from '@/pages/Styleguide';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/operator" replace />} />
      <Route path="/operator" element={<OperatorTasks />} />
      <Route path="/operator/machine" element={<OperatorMachine />} />
      <Route path="/operator/training" element={<OperatorPlaceholder page="training" />} />
      <Route path="/operator/report" element={<OperatorPlaceholder page="report" />} />
      <Route path="/manager" element={<ManagerFleet />} />
      <Route path="/manager/alerts" element={<ManagerPlaceholder title="Alerts" />} />
      <Route path="/manager/health" element={<ManagerPlaceholder title="Health" />} />
      <Route path="/manager/clusters" element={<ManagerPlaceholder title="Clusters" />} />
      <Route path="/manager/training" element={<ManagerPlaceholder title="Training" />} />
      <Route path="/manager/settings" element={<ManagerPlaceholder title="Settings" />} />
      <Route path="/styleguide" element={<Styleguide />} />
      <Route path="*" element={<Navigate to="/operator" replace />} />
    </Routes>
  );
}
