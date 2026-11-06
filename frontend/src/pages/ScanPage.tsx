import { Check, EyeOff, FolderPlus, Play, Search, Trash2, Undo2, Zap } from "lucide-react";
import { useCallback, useState } from "react";
import { api, formatBytes, post, useApi, type TmdbCard } from "../api";
import { ErrorBanner, useJob, useJobFinished, useToast } from "../components";

interface Match { tmdb_id: number; kind: string; title: string; year: number | null; poster_url: string | null; confidence?: number }
interface Candidate {
  id: number; rel_path: string; abs_path: string; size_bytes: number | null; kind: string;
  parsed_title: string; parsed_year: number | null; season: number | null; episode: number | null;
  matches: Match[]; confidence: number;
}

const HIGH = 0.9;

function MatchChoice({ match, selected, onSelect, name }: { match: Match; selected: boolean; onSelect: () => void; name: string }) {
  return (
    <label className={`flex w-28 shrink-0 cursor-pointer flex-col gap-1 rounded-md border p-1.5 text-xs ${selected ? "border-indigo-400 bg-indigo-950/60" : "border-gray-700"}`}>
      <input type="radio" name={name} checked={selected} onChange={onSelect} className="sr-only" />
      {match.poster_url
        ? <img src={match.poster_url.replace("/w500/", "/w185/")} alt="" loading="lazy" className="aspect-[2/3] w-full rounded object-cover" />
        : <div className="aspect-[2/3] w-full rounded bg-gray-700" />}
      <span className="font-medium">{match.title}{match.year ? ` (${match.year})` : ""}</span>
      {match.confidence !== undefined && <span className="text-gray-400">{Math.round(match.confidence * 100)}% match</span>}
    </label>
  );
}

function CandidateRow({ candidate, onDone }: { candidate: Candidate; onDone: () => void }) {
  const toast = useToast();
  const [matches, setMatches] = useState<Match[]>(candidate.matches);
  const [selected, setSelected] = useState<number | null>(candidate.matches[0]?.tmdb_id ?? null);
  const [kind, setKind] = useState(candidate.kind);
  const [query, setQuery] = useState(candidate.parsed_title);
  const [busy, setBusy] = useState(false);

  const search = async () => {
    try {
      const { results } = await api<{ results: TmdbCard[] }>(`/api/discover/search?q=${encodeURIComponent(query)}`);
      setMatches(results.slice(0, 8));
      setSelected(results[0]?.tmdb_id ?? null);
      if (results[0]) setKind(results[0].kind);
      if (!results.length) toast(`No TMDB results for “${query}”.`);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const act = async (url: string, body?: unknown) => {
    setBusy(true);
    try {
      await post(url, body);
      onDone();
    } catch (e) {
      toast((e as Error).message, true);
      setBusy(false);
    }
  };

  const episode = candidate.season !== null
    ? ` · S${String(candidate.season).padStart(2, "0")}${candidate.episode !== null ? `E${String(candidate.episode).padStart(2, "0")}` : ""}`
    : "";
  const chosen = matches.find((m) => m.tmdb_id === selected);

  return (
    <div className="panel space-y-3">
      <div>
        <div className="font-mono text-xs break-all text-gray-300">{candidate.abs_path}</div>
        <div className="mt-1 text-sm">
          Read as <strong>{candidate.parsed_title}</strong>
          {candidate.parsed_year ? ` (${candidate.parsed_year})` : ""}{episode}
          <span className="ml-2 text-xs text-gray-400">{formatBytes(candidate.size_bytes)}</span>
          {candidate.confidence >= HIGH && <span className="ml-2 rounded-full bg-emerald-800 px-2 py-0.5 text-xs">High confidence</span>}
        </div>
      </div>
      {matches.length > 0 && (
        <fieldset className="flex gap-2 overflow-x-auto pb-1">
          <legend className="sr-only">TMDB match for {candidate.rel_path}</legend>
          {matches.map((m) => (
            <MatchChoice
              key={`${m.kind}-${m.tmdb_id}`}
              match={m}
              name={`match-${candidate.id}`}
              selected={m.tmdb_id === selected}
              onSelect={() => { setSelected(m.tmdb_id); setKind(m.kind); }}
            />
          ))}
        </fieldset>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-primary" disabled={busy || !chosen} onClick={() => act(`/api/scan/candidates/${candidate.id}/accept`, { tmdb_id: selected, kind })}>
          <Check size={16} /> Confirm match
        </button>
        <button className="btn" disabled={busy} title="Add using only the title and year read from the filename" onClick={() => act(`/api/scan/candidates/${candidate.id}/accept`, { kind: candidate.kind })}>
          Add without TMDB
        </button>
        <button className="btn" disabled={busy} onClick={() => act(`/api/scan/candidates/${candidate.id}/ignore`)}>
          <EyeOff size={16} /> Ignore
        </button>
        <form className="flex min-w-56 flex-1 gap-2" onSubmit={(e) => { e.preventDefault(); search(); }}>
          <input aria-label="Search TMDB for another match" className="input" value={query} onChange={(e) => setQuery(e.target.value)} />
          <button className="btn"><Search size={16} /> Find match</button>
        </form>
      </div>
    </div>
  );
}

export function ScanPage() {
  const toast = useToast();
  const { job, refresh } = useJob();
  const roots = useApi<any[]>("/api/scan/roots");
  const mounts = useApi<any[]>("/api/volumes");
  const [tab, setTab] = useState<"pending" | "ignored">("pending");
  const candidates = useApi<Candidate[]>(`/api/scan/candidates?status=${tab}`);
  const [path, setPath] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);

  useJobFinished(useCallback((finished) => {
    if (finished.name !== "Drive scan") return;
    roots.reload();
    candidates.reload();
    if (finished.error) {
      toast(`Scan failed: ${finished.error}`, true);
    } else {
      const r = finished.result;
      toast(`Scan finished: ${r.files} media files, ${r.new} new, ${r.known} already in the database.`);
      if (r.tmdb_error) toast(`Files were queued without TMDB suggestions: ${r.tmdb_error}`, true);
    }
  }, [roots.reload, candidates.reload, toast]));

  const addRoot = async (value: string) => {
    if (!value.trim()) return;
    try {
      await post("/api/scan/roots", { path: value.trim() });
      setPath("");
      roots.reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const run = async (id: number) => {
    try {
      await post(`/api/scan/roots/${id}/run`);
      refresh();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const removeRoot = async (id: number) => {
    await post(`/api/scan/roots/${id}`, undefined, "DELETE");
    roots.reload();
    candidates.reload();
  };

  const acceptHigh = async () => {
    setBulkBusy(true);
    try {
      const result = await post("/api/scan/candidates/accept-high-confidence");
      toast(`Accepted ${result.accepted} high-confidence matches.`);
      result.errors.slice(0, 3).forEach((message: string) => toast(message, true));
      candidates.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBulkBusy(false);
    }
  };

  const unignore = async (id: number) => {
    await post(`/api/scan/candidates/${id}/unignore`);
    candidates.reload();
  };

  const list = candidates.data ?? [];
  const highCount = list.filter((c) => c.confidence >= HIGH).length;
  const rootPaths = new Set((roots.data ?? []).map((r) => r.current_path));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Drive Scan</h1>
        <p className="text-sm text-gray-400">Scanning only reads your drives. New files wait here until you confirm them.</p>
      </div>

      <section className="panel space-y-3">
        <h2 className="font-bold">Scan folders</h2>
        {roots.error && <ErrorBanner message={roots.error} />}
        {roots.data?.length === 0 && <p className="text-sm text-gray-400">No folders yet. Add one below.</p>}
        {roots.data?.map((root) => (
          <div key={root.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-700 pb-2 text-sm">
            <div className="min-w-0">
              <div className="font-medium">{root.label}
                {!root.connected && <span className="ml-2 rounded-full bg-amber-500 px-2 py-0.5 text-xs font-semibold text-gray-950">Not connected</span>}
              </div>
              <div className="font-mono text-xs break-all text-gray-400">{root.current_path ?? root.last_path}</div>
            </div>
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={!root.connected || job?.running} onClick={() => run(root.id)}><Play size={16} /> Scan</button>
              <button className="btn" aria-label={`Remove ${root.label}`} title="Stop scanning this folder. Nothing on the drive changes." onClick={() => removeRoot(root.id)}><Trash2 size={16} /></button>
            </div>
          </div>
        ))}
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); addRoot(path); }}>
          <input aria-label="Folder path" className="input font-mono" placeholder="/run/media/you/Drive/Movies   or   E:\Movies" value={path} onChange={(e) => setPath(e.target.value)} />
          <button className="btn"><FolderPlus size={16} /> Add folder</button>
        </form>
        {mounts.data && mounts.data.some((m) => !rootPaths.has(m.path)) && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
            Connected drives:
            {mounts.data.filter((m) => !rootPaths.has(m.path)).map((m) => (
              <button key={m.volume_id + m.path} className="rounded-full border border-gray-600 px-2 py-0.5 font-mono text-gray-200 hover:bg-gray-700" onClick={() => setPath(m.path)}>
                {m.path}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-1" role="tablist">
            {(["pending", "ignored"] as const).map((name) => (
              <button key={name} role="tab" aria-selected={tab === name} className={`btn ${tab === name ? "btn-primary" : ""}`} onClick={() => setTab(name)}>
                {name === "pending" ? "Review queue" : "Ignored"}
              </button>
            ))}
          </div>
          {tab === "pending" && highCount > 0 && (
            <button className="btn" disabled={bulkBusy} onClick={acceptHigh}>
              <Zap size={16} /> {bulkBusy ? "Accepting…" : `Accept ${highCount} high-confidence`}
            </button>
          )}
        </div>
        {candidates.error && <ErrorBanner message={candidates.error} />}
        {!candidates.loading && list.length === 0 && (
          <p className="py-10 text-center text-gray-400">{tab === "pending" ? "Nothing waiting for review." : "No ignored files."}</p>
        )}
        {tab === "pending"
          ? list.map((c) => <CandidateRow key={c.id} candidate={c} onDone={candidates.reload} />)
          : list.map((c) => (
              <div key={c.id} className="panel flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs break-all text-gray-300">{c.abs_path}</span>
                <button className="btn" onClick={() => unignore(c.id)}><Undo2 size={16} /> Back to queue</button>
              </div>
            ))}
      </section>
    </div>
  );
}
