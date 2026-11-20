import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useParams,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import { Shell } from "./components/Shell";
import { Spinner } from "./components/ui";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { Attendance } from "./pages/Attendance";
import { Compliance } from "./pages/Compliance";
import { Dashboard } from "./pages/Dashboard";
import { DutyBoard } from "./pages/DutyBoard";
import { Exposure } from "./pages/Exposure";
import { InternDetail } from "./pages/InternDetail";
import { Interns } from "./pages/Interns";
import { Login } from "./pages/Login";
import { MyAttendance } from "./pages/MyAttendance";
import { MyClock } from "./pages/MyClock";
import { MyProgress } from "./pages/MyProgress";
import { Reports } from "./pages/Reports";
import { Schedule } from "./pages/Schedule";
import { Settings } from "./pages/Settings";
import { Shifts } from "./pages/Shifts";
import { Timesheets } from "./pages/Timesheets";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // A 401 is handled globally by signing out, and a 403 will not succeed
      // on retry either; retrying those just delays the error.
      retry: (count, error) =>
        error instanceof ApiError && (error.isAuthError || error.isForbidden)
          ? false
          : count < 2,
    },
  },
});

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner label="Restoring session…" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** Interns land on their time clock; everyone else on the dashboard. */
function Home() {
  const { hasRole, can } = useAuth();
  if (hasRole("INTERN") && !can("dashboard.read")) {
    return <Navigate to="/me/clock" replace />;
  }
  return <Dashboard />;
}

function InternSchedule() {
  const { id } = useParams<{ id: string }>();
  return <Schedule internId={Number(id)} />;
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Home />} />
        <Route path="/duty-board" element={<DutyBoard />} />
        <Route path="/compliance" element={<Compliance />} />
        <Route path="/interns" element={<Interns />} />
        <Route path="/interns/:id" element={<InternDetail />} />
        <Route path="/interns/:id/schedule" element={<InternSchedule />} />
        <Route path="/attendance" element={<Attendance />} />
        <Route path="/timesheets" element={<Timesheets />} />
        <Route path="/exposure" element={<Exposure />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/shifts" element={<Shifts />} />
        <Route path="/settings" element={<Settings />} />

        {/* Intern self-service */}
        <Route path="/me/clock" element={<MyClock />} />
        <Route path="/me/progress" element={<MyProgress />} />
        <Route path="/me/schedule" element={<Schedule />} />
        <Route path="/me/attendance" element={<MyAttendance />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
