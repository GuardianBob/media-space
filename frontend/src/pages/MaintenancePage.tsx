import { ExternalLink, GitCompare, X } from "lucide-react";
import { ReactNode, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, formatBytes, languageName, useApi } from "../api";
import { ErrorBanner, LibraryBadge, LocationRow, Poster } from "../components";

interface Member {
  id: number; kind: string; title: string; year: number | null; tmdb_id: number | null;
  source: string; on_jellyfin: boolean; removed_from_jellyfin: boolean; poster_url: string | null;
  resolution: string | null; file_count: number; total_bytes: number;
}
interface Group { title: string; kind: string; items: Member[] }
interface Result { groups: Group[]; total_groups: number; total_items: number }

const MODES: [string, string][] = [
  ["title_year", "Same title and year"],
  ["title", "Same title, any year"],
  ["tmdb", "Same TMDB ID"],
];

const date = (value: string | null) => (value ? new Date(value).toLocaleDateString() : "");

/** One comparable row: a label and a text value per title. Rows whose values differ are highlighted. */
function rowsFor(d: any): [string, string][] {
  const episodes = (d.seasons ?? []).reduce((n: number, s: any) => n + s.episodes.length, 0);
  const files = d.locations.length + (d.seasons ?? []).reduce(
    (n: number, s: any) => n + s.episodes.reduce((m: number, e: any) => m + e.locations.length, 0), 0);
  const bytes = [
    ...d.locations,
    ...(d.seasons ?? []).flatMap((s: any) => s.episodes.flatMap((e: any) => e.locations)),
  ].reduce((n: number, l: any) => n + (l.size_bytes ?? 0), 0);
  return [
    ["Type", d.kind === "movie" ? "Movie" : "Series"],
    ["Year", d.year?.toString() ?? ""],
    ["Release date", d.release_date ?? ""],
    ["Source", d.source],
    ["On Jellyfin", d.on_jellyfin ? "Yes" : d.removed_from_jellyfin ? "Removed" : "No"],
    ["TMDB ID", d.tmdb_id?.toString() ?? ""],
    ["IMDb ID", d.imdb_id ?? ""],
    ["Resolution", d.resolution ?? ""],
    ["Runtime", d.runtime_minutes ? `${d.runtime_minutes} min` : ""],
    ["Rating", d.community_rating?.toFixed(1) ?? ""],
    ["Original language", d.original_language ? languageName(d.original_language) : ""],
    ["Country", d.country ?? ""],
    ["Genres", d.genres.join(", ")],
    ["Cast listed", d.credits.length ? String(d.credits.length) : ""],
    ["Summary", d.summary ?? ""],
    ["Episodes", d.kind === "series" ? String(episodes) : ""],
    ["Files", files ? `${files} · ${formatBytes(bytes)}` : "None"],
    ["Added", date(d.added_at)],
    ["Updated", date(d.updated_at)],
  ];
}

function Compare({ ids, onClose }: { ids: number[]; onClose: () => void }) {
  const [details, setDetails] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setDetails(null);
    Promise.all(ids.map((id) => api(`/api/items/${id}`)))
      .then((all) => current && setDetails(all))
      .catch((e: Error) => current && setError(e.message));
    return () => { current = false; };
  }, [ids.join(",")]);

  if (error) return <ErrorBanner message={error} />;
  if (!details) return <p className="py-6 text-center text-gray-400">Loading…</p>;

  const table = details.map(rowsFor);
  const columns = `10rem repeat(${details.length}, minmax(12rem, 1fr))`;
  const Row = ({ label, children, differs }: { label: string; children: ReactNode; differs?: boolean }) => (
    <div className={`contents ${differs ? "[&>*]:bg-amber-500/10" : ""}`}>
      <div className="border-t border-gray-700 px-2 py-1.5 text-xs font-semibold text-gray-400">{label}</div>
      {children}
    </div>
  );

  return (
    <div className="panel space-y-3" aria-label="Comparison">
      <div className="flex items-center justify-between">
        <h3 className="font-bold">Compare {details.length} records</h3>
        <button className="btn" onClick={onClose}><X size={16} /> Close</button>
      </div>
      <p className="text-xs text-gray-400">Rows with differences are highlighted.</p>
      <div className="overflow-x-auto">
        <div className="grid min-w-max items-start text-sm" style={{ gridTemplateColumns: columns }}>
          <div />
          {details.map((d) => (
            <div key={d.id} className="space-y-2 px-2 pb-2">
              <div className="w-28"><Poster src={d.poster_url} alt={d.title} /></div>
              <Link to={`/item/${d.id}`} className="flex items-center gap-1 font-semibold text-indigo-300 hover:underline">
                {d.title} <ExternalLink size={12} />
              </Link>
              <LibraryBadge item={d} compact />
              <div className="text-xs text-gray-400">Record #{d.id}</div>
            </div>
          ))}
          {table[0].map(([label], r) => {
            const values = table.map((t) => t[r][1]);
            const differs = new Set(values).size > 1;
            return (
              <Row key={label} label={label} differs={differs}>
                {values.map((v, i) => (
                  <div key={i} className="border-t border-gray-700 px-2 py-1.5 break-words">
                    {label === "Summary" && v.length > 160 ? `${v.slice(0, 160)}…` : v || <span className="text-gray-500">—</span>}
                  </div>
                ))}
              </Row>
            );
          })}
          <div className="border-t border-gray-700 px-2 py-1.5 text-xs font-semibold text-gray-400">File locations</div>
          {details.map((d) => (
            <div key={d.id} className="divide-y divide-gray-700 border-t border-gray-700 px-2">
              {d.locations.length === 0 && (d.seasons ?? []).length === 0 && <span className="text-gray-500">None</span>}
              {d.locations.map((l: any) => <LocationRow key={l.id} location={l} />)}
              {d.kind === "series" && d.locations.length === 0 && d.seasons.length > 0 && (
                <span className="text-xs text-gray-400">Per-episode files: see the title page.</span>
              )}
            </div>
          ))}
        </div>
      </div>
      <p className="text-xs text-gray-400">To remove a record, open its title page and use Delete there.</p>
    </div>
  );
}

export function MaintenancePage() {
  const [params, setParams] = useSearchParams();
  const mode = params.get("mode") ?? "title_year";
  const kind = params.get("kind") ?? "";
  const source = params.get("source") ?? "";
  const { data, error, loading } = useApi<Result>(
    `/api/maintenance/duplicates?${new URLSearchParams({ mode, kind, source })}`,
  );
  const [selected, setSelected] = useState<Record<string, number[]>>({});
  const [comparing, setComparing] = useState<string | null>(null);

  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    setParams(next, { replace: true });
    setComparing(null);
  };

  const groupKey = (g: Group) => g.items.map((i) => i.id).join(",");
  const chosen = (g: Group) => selected[groupKey(g)] ?? g.items.map((i) => i.id);
  const toggle = (g: Group, id: number) => {
    const current = chosen(g);
    setSelected((all) => ({
      ...all, [groupKey(g)]: current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
    }));
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">Maintenance</h1>
        <p className="text-sm text-gray-400">Find duplicate titles and compare their records side by side. Nothing here changes your library.</p>
      </div>

      <div className="flex flex-wrap gap-2">
        <select aria-label="Match duplicates by" className="input !w-auto" value={mode} onChange={(e) => setFilter("mode", e.target.value === "title_year" ? "" : e.target.value)}>
          {MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select aria-label="Type" className="input !w-auto" value={kind} onChange={(e) => setFilter("kind", e.target.value)}>
          <option value="">Movies and series</option>
          <option value="movie">Movies</option>
          <option value="series">Series</option>
        </select>
        <select aria-label="Source" className="input !w-auto" value={source} onChange={(e) => setFilter("source", e.target.value)}>
          <option value="">Any source</option>
          <option value="jellyfin">Includes a Jellyfin record</option>
          <option value="other">Includes a non-Jellyfin record</option>
        </select>
      </div>

      {error && <ErrorBanner message={error} />}
      {data && (
        <p className="text-sm text-gray-300" role="status">
          {data.total_groups === 0 ? "No duplicates found." : `${data.total_groups} groups, ${data.total_items} records.`}
        </p>
      )}
      {loading && !data && <p className="py-6 text-center text-gray-400">Looking for duplicates…</p>}

      {data?.groups.map((g) => {
        const key = groupKey(g);
        const ids = chosen(g);
        return (
          <section key={key} className="panel space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-bold">{g.title} <span className="text-sm font-normal text-gray-400">· {g.items.length} records</span></h2>
              <button className="btn" disabled={ids.length < 2} onClick={() => setComparing(comparing === key ? null : key)}>
                <GitCompare size={16} /> {comparing === key ? "Hide comparison" : `Compare ${ids.length}`}
              </button>
            </div>
            <ul className="divide-y divide-gray-700">
              {g.items.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                  <input type="checkbox" aria-label={`Include ${m.title} #${m.id} in comparison`} checked={ids.includes(m.id)} onChange={() => toggle(g, m.id)} />
                  <Link to={`/item/${m.id}`} className="font-medium text-indigo-300 hover:underline">
                    {m.title}{m.year ? ` (${m.year})` : ""}
                  </Link>
                  <LibraryBadge item={m} compact />
                  <span className="text-xs text-gray-400">
                    {[m.resolution, m.file_count ? `${m.file_count} file${m.file_count > 1 ? "s" : ""}` : "no files", formatBytes(m.total_bytes), m.tmdb_id && `TMDB ${m.tmdb_id}`].filter(Boolean).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
            {comparing === key && <Compare ids={ids} onClose={() => setComparing(null)} />}
          </section>
        );
      })}
    </div>
  );
}
