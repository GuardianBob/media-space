import { ArrowDownWideNarrow, ArrowUpNarrowWide, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, languageName, post, useApi, type Card } from "../api";
import {
  CardGrid, CardPlaceholders, ErrorBanner, LibraryCard, ScrollSentinel, useJob, useJobFinished, useToast,
} from "../components";

const SIZE = 40;
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

// Each sort's natural starting direction matches the backend's default.
const SORT_OPTIONS: Record<string, { label: string; order: "asc" | "desc" }> = {
  title: { label: "Title", order: "asc" },
  release: { label: "Release date", order: "desc" },
  added: { label: "Date added", order: "desc" },
  rating: { label: "Rating", order: "desc" },
  runtime: { label: "Runtime", order: "desc" },
  resolution: { label: "Resolution", order: "desc" },
};

function directionLabel(sort: string, order: string): string {
  const asc = order === "asc";
  if (sort === "title") return asc ? "A–Z" : "Z–A";
  if (sort === "release" || sort === "added") return asc ? "Oldest" : "Newest";
  if (sort === "rating" || sort === "resolution") return asc ? "Lowest" : "Highest";
  return asc ? "Shortest" : "Longest";
}

export function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const library = params.get("library") ?? "";
  const sort = params.get("sort") ?? "title";
  const order = params.get("order") ?? SORT_OPTIONS[sort]?.order ?? "asc";
  const letter = params.get("letter") ?? "";
  const genre = params.get("genre") ?? "";
  const year = params.get("year") ?? "";
  const resolution = params.get("resolution") ?? "";
  const language = params.get("language") ?? "";
  const { data: options, reload: reloadOptions } = useApi<{ genres: string[]; years: number[]; resolutions: string[]; languages: string[] }>("/api/items/filter-options");
  const [items, setItems] = useState<Card[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const pageRef = useRef(1);
  const requestRef = useRef(0);
  const toast = useToast();
  const { job, refresh } = useJob();

  const load = useCallback(async (page: number) => {
    const request = ++requestRef.current;
    setLoading(true);
    try {
      const query = new URLSearchParams({ kind, library, sort, order, letter, genre, resolution, language, page: String(page), size: String(SIZE) });
      if (year) query.set("year", year);
      const data = await api(`/api/items?${query}`);
      if (request !== requestRef.current) return;  // filters changed meanwhile
      setItems((current) => (page === 1 ? data.items : [...current, ...data.items]));
      setTotal(data.total);
      pageRef.current = page;
      setError(null);
    } catch (e) {
      if (request === requestRef.current) setError((e as Error).message);
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }, [kind, library, sort, order, letter, genre, year, resolution, language]);

  useEffect(() => {
    load(1);
  }, [load, version]);

  useJobFinished(useCallback((finished) => {
    setVersion((v) => v + 1);
    reloadOptions();
    if (finished.name !== "Jellyfin sync") return;
    if (finished.error) {
      toast(`Sync failed: ${finished.error}`, true);
    } else {
      const r = finished.result;
      toast(`Sync finished: ${r.added} added, ${r.updated} updated, ${r.removed} no longer on Jellyfin, ${r.excluded} excluded${r.filled ? `, ${r.filled} filled in from TMDB` : ""}.`);
      if (r.fill_error) toast(`Sync finished, but filling in from TMDB stopped: ${r.fill_error}`, true);
    }
  }, [toast, reloadOptions]));

  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  };

  const startSync = async () => {
    try {
      await post("/api/sync/jellyfin");
      refresh();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const hasMore = total !== null && items.length < total;
  const loadMore = useCallback(() => load(pageRef.current + 1), [load]);
  const hasFilters = Boolean(kind || library || letter || genre || year || resolution || language);
  const isEmptyLibrary = total === 0 && !hasFilters;

  const changeSort = (value: string) => {
    const next = new URLSearchParams(params);
    if (value === "title") next.delete("sort");
    else next.set("sort", value);
    next.delete("order");  // each sort starts in its natural direction
    setParams(next, { replace: true });
  };

  const toggleOrder = () => setFilter("order", order === "asc" ? "desc" : "asc");

  const clearFilters = () => {
    const next = new URLSearchParams(params);
    ["kind", "library", "letter", "genre", "year", "resolution", "language"].forEach((name) => next.delete(name));
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="page-title">
          Library {total !== null && <span className="text-base font-normal text-gray-400">{total.toLocaleString()} titles</span>}
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Media type" className="input !w-auto" value={kind} onChange={(e) => setFilter("kind", e.target.value)}>
            <option value="">Movies & Series</option>
            <option value="movie">Movies</option>
            <option value="series">Series</option>
          </select>
          <select aria-label="Availability" className="input !w-auto" value={library} onChange={(e) => setFilter("library", e.target.value)}>
            <option value="">Everything</option>
            <option value="jellyfin">On Jellyfin</option>
            <option value="not_jellyfin">Not on Jellyfin</option>
          </select>
          <select aria-label="Genre" className="input !w-auto" value={genre} onChange={(e) => setFilter("genre", e.target.value)}>
            <option value="">All genres</option>
            {options?.genres.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <select aria-label="Year" className="input !w-auto" value={year} onChange={(e) => setFilter("year", e.target.value)}>
            <option value="">All years</option>
            {options?.years.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <select aria-label="Resolution" className="input !w-auto" value={resolution} onChange={(e) => setFilter("resolution", e.target.value)}>
            <option value="">All resolutions</option>
            {options?.resolutions.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <select aria-label="Original language" className="input !w-auto" value={language} onChange={(e) => setFilter("language", e.target.value)}>
            <option value="">All languages</option>
            {options?.languages?.map((code) => <option key={code} value={code}>{languageName(code)}</option>)}
          </select>
          <div className="flex">
            <select aria-label="Sort by" className="input !w-auto rounded-r-none" value={sort} onChange={(e) => changeSort(e.target.value)}>
              {Object.entries(SORT_OPTIONS).map(([value, option]) => <option key={value} value={value}>{option.label}</option>)}
            </select>
            <button
              className="btn rounded-l-none border-l-0"
              onClick={toggleOrder}
              aria-label={`Sort direction: ${directionLabel(sort, order)}. Click to reverse.`}
              title="Reverse sort direction"
            >
              {order === "asc" ? <ArrowUpNarrowWide size={16} /> : <ArrowDownWideNarrow size={16} />}
              {directionLabel(sort, order)}
            </button>
          </div>
          <button className="btn btn-primary" onClick={startSync} disabled={job?.running}>
            <RefreshCw size={16} className={job?.running ? "animate-spin" : ""} />
            Sync Jellyfin
          </button>
        </div>
      </div>

      <nav aria-label="Filter by first letter" className="flex flex-wrap items-center gap-1">
        {["", "#", ...LETTERS].map((value) => (
          <button
            key={value || "all"}
            aria-pressed={letter === value}
            onClick={() => setFilter("letter", value)}
            className={`h-7 rounded px-2 text-xs font-semibold transition ${
              letter === value ? "bg-indigo-600 text-white" : "text-gray-300 hover:bg-gray-700"
            }`}
          >
            {value || "All"}
          </button>
        ))}
        {hasFilters && (
          <button className="ml-2 inline-flex h-7 items-center gap-1 rounded px-2 text-xs text-indigo-300 hover:bg-gray-700" onClick={clearFilters}>
            <X size={14} /> Clear filters
          </button>
        )}
      </nav>

      {error && <ErrorBanner message={error} />}

      {isEmptyLibrary && !loading && (
        <div className="py-24 text-center text-gray-400">
          <p className="text-xl">Your library is empty.</p>
          <p className="mt-2">Sync Jellyfin, <Link className="text-indigo-300 underline" to="/scan">scan a drive</Link>, or <Link className="text-indigo-300 underline" to="/csv">import a CSV</Link>.</p>
        </div>
      )}
      {total === 0 && !isEmptyLibrary && !loading && (
        <div className="py-24 text-center text-xl text-gray-400">No titles match these filters.</div>
      )}

      <CardGrid>
        {items.map((item) => <LibraryCard key={item.id} item={item} />)}
        {loading && <CardPlaceholders />}
      </CardGrid>
      <ScrollSentinel onVisible={loadMore} enabled={hasMore && !loading && !error} />
    </div>
  );
}
