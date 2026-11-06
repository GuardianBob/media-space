import { Ban, Compass, Dices, FileSpreadsheet, HardDrive, Library, Loader2, Search, Settings as Cog } from "lucide-react";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes, useNavigate, useSearchParams } from "react-router-dom";
import { JobProvider, ToastProvider, useJob } from "./components";
import "./index.css";
import { CsvPage } from "./pages/CsvPage";
import { DiscoverList, DiscoverPage, SearchPage } from "./pages/DiscoverPages";
import { ExcludedPage } from "./pages/ExcludedPage";
import { ItemPage, TmdbPage } from "./pages/DetailPages";
import { LibraryPage } from "./pages/LibraryPage";
import { ScanPage } from "./pages/ScanPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WatchPage } from "./pages/WatchPage";

const NAV = [
  { to: "/", label: "Library", icon: Library },
  { to: "/watch", label: "What to Watch", icon: Dices },
  { to: "/discover", label: "Discover", icon: Compass },
  { to: "/scan", label: "Drive Scan", icon: HardDrive },
  { to: "/csv", label: "CSV Import", icon: FileSpreadsheet },
  { to: "/excluded", label: "Excluded", icon: Ban },
  { to: "/settings", label: "Settings", icon: Cog },
];

function SearchBox() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [value, setValue] = useState(params.get("q") ?? "");
  useEffect(() => setValue(params.get("q") ?? ""), [params]);
  return (
    <form
      role="search"
      className="relative flex-1"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) navigate(`/search?q=${encodeURIComponent(value.trim())}`);
      }}
    >
      <Search size={18} className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-gray-300" />
      <input
        type="search"
        aria-label="Search your library and TMDB"
        placeholder="Search Movies & Series"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full rounded-full border border-gray-600 bg-gray-900/80 py-2 pr-4 pl-11 text-white placeholder-gray-300 hover:border-gray-500 focus:border-gray-400 focus:outline-none"
      />
    </form>
  );
}

function JobIndicator() {
  const { job } = useJob();
  if (!job?.running) return null;
  return (
    <div className="flex items-center gap-2 border-t border-gray-700 p-3 text-xs text-gray-300" role="status">
      <Loader2 size={14} className="shrink-0 animate-spin" />
      <span className="min-w-0 break-words">
        <strong className="block">{job.name}</strong>
        {job.message}
      </span>
    </div>
  );
}

function Shell() {
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-14 shrink-0 flex-col border-r border-gray-700 bg-gray-800 md:w-56">
        <div className="hidden px-5 py-5 text-lg font-bold text-indigo-300 md:block">Media Manager</div>
        <nav className="flex-1 space-y-1 p-2 pt-4 md:pt-0">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              title={label}
              className={({ isActive }) =>
                `flex items-center justify-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition md:justify-start ${
                  isActive ? "bg-indigo-600 text-white" : "text-gray-300 hover:bg-gray-700"
                }`
              }
            >
              <Icon size={18} className="shrink-0" />
              <span className="hidden md:inline">{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="hidden md:block">
          <JobIndicator />
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex items-center bg-gray-900/90 px-4 py-3 backdrop-blur md:px-6">
          <SearchBox />
        </header>
        <main className="px-4 pb-10 md:px-6">
          <Routes>
            <Route path="/" element={<LibraryPage />} />
            <Route path="/item/:id" element={<ItemPage />} />
            <Route path="/watch" element={<WatchPage />} />
            <Route path="/discover" element={<DiscoverPage />} />
            <Route path="/discover/list/:list" element={<DiscoverList />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/tmdb/:kind/:id" element={<TmdbPage />} />
            <Route path="/scan" element={<ScanPage />} />
            <Route path="/csv" element={<CsvPage />} />
            <Route path="/excluded" element={<ExcludedPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <JobProvider>
          <Shell />
        </JobProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
);
