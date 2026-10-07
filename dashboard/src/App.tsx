import { lazy, Suspense } from 'react';
import { Routes, Route, NavLink } from 'react-router-dom';
import { ErrorBoundary } from './components/ErrorBoundary';

const Overview = lazy(() => import('./pages/Overview'));
const Traces = lazy(() => import('./pages/Traces'));
const TraceDetail = lazy(() => import('./pages/TraceDetail'));
const Evaluations = lazy(() => import('./pages/Evaluations'));

function LoadingFallback() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="text-gray-400">Loading...</div>
    </div>
  );
}

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 rounded-md text-sm font-medium ${
    isActive ? 'bg-gray-700 text-white' : 'text-gray-300 hover:text-white'
  }`;

function App() {
  return (
    <div className="min-h-screen bg-gray-900">
      <nav className="bg-gray-800 border-b border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between min-h-16 py-3 sm:py-0">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-0">
              <NavLink to="/" className="text-xl font-bold text-white">
                LLM Observatory
              </NavLink>
              <div className="sm:ml-10 flex items-baseline gap-4">
                <NavLink to="/" end className={navLinkClass}>
                  Overview
                </NavLink>
                <NavLink to="/traces" className={navLinkClass}>
                  Traces
                </NavLink>
                <NavLink to="/evaluations" className={navLinkClass}>
                  Evaluations
                </NavLink>
              </div>
            </div>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <ErrorBoundary>
          <Suspense fallback={<LoadingFallback />}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/traces" element={<Traces />} />
              <Route path="/traces/:traceId" element={<TraceDetail />} />
              <Route path="/evaluations" element={<Evaluations />} />
              <Route path="*" element={
                <div className="text-center py-20">
                  <h1 className="text-4xl font-bold text-white mb-4">404</h1>
                  <p className="text-gray-400">Page not found</p>
                </div>
              } />
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default App;
