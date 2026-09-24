import { Navigate, Route, Routes } from 'react-router-dom';
import { useDemoBus } from '@/data/demoBus';
import { useOfflineSync } from '@/data/hooks';
import { Demo } from '@/pages/Demo';
import { ManagerAlerts } from '@/pages/manager/Alerts';
import { ManagerClusters } from '@/pages/manager/Clusters';
import { ManagerFleet } from '@/pages/manager/Fleet';
import { ManagerGeofences } from '@/pages/manager/Geofences';
import { ManagerMachine } from '@/pages/manager/MachineDetail';
import { ManagerMaintenance } from '@/pages/manager/Maintenance';
import { ManagerSafety } from '@/pages/manager/SafetyAnalytics';
import { OperatorHandover } from '@/pages/operator/Handover';
import { OperatorLogin } from '@/pages/operator/Login';
import { OperatorMachine } from '@/pages/operator/Machine';
import { OperatorReport } from '@/pages/operator/Report';
import { OperatorSafety } from '@/pages/operator/Safety';
import { OperatorTasks } from '@/pages/operator/Tasks';
import { OperatorTraining } from '@/pages/operator/Training';
import { Styleguide } from '@/pages/Styleguide';

export function App() {
  useDemoBus();
  useOfflineSync();
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/operator" replace />} />
      <Route path="/operator/login" element={<OperatorLogin />} />
      <Route path="/operator/handover" element={<OperatorHandover />} />
      <Route path="/operator" element={<OperatorTasks />} />
      <Route path="/operator/machine" element={<OperatorMachine />} />
      <Route path="/operator/safety" element={<OperatorSafety />} />
      <Route path="/operator/training" element={<OperatorTraining />} />
      <Route path="/operator/report" element={<OperatorReport />} />
      <Route path="/manager" element={<ManagerFleet />} />
      <Route path="/manager/alerts" element={<ManagerAlerts />} />
      <Route path="/manager/machines/:id" element={<ManagerMachine />} />
      <Route path="/manager/health" element={<ManagerMaintenance />} />
      <Route path="/manager/clusters" element={<ManagerClusters />} />
      <Route path="/manager/safety" element={<ManagerSafety />} />
      <Route path="/manager/geofences" element={<ManagerGeofences />} />
      {/* Hidden: not linked from either app */}
      <Route path="/demo" element={<Demo />} />
      <Route path="/styleguide" element={<Styleguide />} />
      <Route path="*" element={<Navigate to="/operator" replace />} />
    </Routes>
  );
}
