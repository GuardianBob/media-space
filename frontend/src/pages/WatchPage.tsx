import { Dices, X } from "lucide-react";
import { useEffect, useState } from "react";
import { languageName, post, useApi, type Card } from "../api";
import { CardGrid, CardPlaceholders, ErrorBanner, LibraryCard } from "../components";

interface Filters {
  kind: string;
  availability: string;
  year_min: string;
  year_max: string;
  genres: string[];
  resolutions: string[];
  languages: string[];
  min_rating: string;
  max_runtime: string;
  count: number;
}
interface Results { items: Card[]; matches: number }

const DEFAULTS: Filters = {
  kind: "", availability: "", year_min: "", year_max: "", genres: [], resolutions: [], languages: [],
  min_rating: "", max_runtime: "", count: 10,
};
// Kept for the session, so opening a title and coming back returns to the same list.
const STORAGE_KEY = "what-to-watch";

function loadSaved(): { filters: Filters; results: Results | null } {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "null");
    if (saved) return { filters: { ...DEFAULTS, ...saved.filters }, results: saved.results };
  } catch {
    // unreadable saved state just starts fresh
  }
  return { filters: DEFAULTS, results: null };
}

function Segmented<T extends string | number>({ label, options, value, onChange }: {
  label: string; options: [T, string][]; value: T; onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex w-fit overflow-hidden rounded-md border border-gray-600">
      {options.map(([option, text]) => (
        <button
          key={String(option)}
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={`px-3 py-1.5 text-sm font-medium transition ${
            value === option ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-200 hover:bg-gray-700"
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

function Chips({ label, options, selected, onChange, display = (o) => o }: {
  label: string; options: string[]; selected: string[]; onChange: (selected: string[]) => void;
  display?: (option: string) => string;
}) {
  const toggle = (option: string) =>
    onChange(selected.includes(option) ? selected.filter((s) => s !== option) : [...selected, option]);
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((option) => (
        <button
          key={option}
          aria-pressed={selected.includes(option)}
          onClick={() => toggle(option)}
          className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
            selected.includes(option)
              ? "border-indigo-400 bg-indigo-600 text-white"
              : "border-gray-600 bg-gray-800 text-gray-200 hover:bg-gray-700"
          }`}
        >
          {display(option)}
        </button>
      ))}
    </div>
  );
}

const FieldLabel = ({ children }: { children: string }) => (
  <div className="mb-1.5 text-sm font-semibold">{children}</div>
);

export function WatchPage() {
  const { data: options } = useApi<{ genres: string[]; years: number[]; resolutions: string[]; languages: string[] }>("/api/items/filter-options");
  const [saved] = useState(loadSaved);
  const [filters, setFilters] = useState<Filters>(saved.filters);
  const [results, setResults] = useState<Results | null>(saved.results);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ filters, results }));
  }, [filters, results]);

  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => setFilters((all) => ({ ...all, [key]: value }));
  const toNumber = (text: string) => (text ? Number(text) : null);

  const generate = async () => {
    setLoading(true);
    setError(null);
    try {
      setResults(await post<Results>("/api/what-to-watch", {
        kind: filters.kind,
        availability: filters.availability,
        year_min: toNumber(filters.year_min),
        year_max: toNumber(filters.year_max),
        genres: filters.genres,
        resolutions: filters.resolutions,
        languages: filters.languages,
        min_rating: toNumber(filters.min_rating),
        max_runtime: toNumber(filters.max_runtime),
        count: filters.count,
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const years = options?.years ?? [];
  const isFiltered = JSON.stringify({ ...filters, count: 0 }) !== JSON.stringify({ ...DEFAULTS, count: 0 });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="page-title">What to Watch</h1>
        <p className="text-sm text-gray-400">Set any filters you like, then get a random list from your database.</p>
      </div>

      <section className="panel space-y-4">
        <div className="flex flex-wrap gap-x-8 gap-y-4">
          <div>
            <FieldLabel>Type</FieldLabel>
            <Segmented label="Type" value={filters.kind} onChange={(v) => set("kind", v)} options={[["", "All"], ["movie", "Movies"], ["series", "Series"]]} />
          </div>
          <div>
            <FieldLabel>Availability</FieldLabel>
            <Segmented label="Availability" value={filters.availability} onChange={(v) => set("availability", v)} options={[["", "Everything"], ["jellyfin", "On Jellyfin"], ["not_jellyfin", "Not on Jellyfin"]]} />
          </div>
          <div>
            <FieldLabel>Year range</FieldLabel>
            <div className="flex items-center gap-2">
              <select aria-label="From year" className="input !w-auto" value={filters.year_min} onChange={(e) => set("year_min", e.target.value)}>
                <option value="">Any</option>
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
              <span className="text-sm text-gray-400">to</span>
              <select aria-label="To year" className="input !w-auto" value={filters.year_max} onChange={(e) => set("year_max", e.target.value)}>
                <option value="">Any</option>
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </div>
          </div>
          <div>
            <FieldLabel>Minimum rating</FieldLabel>
            <select aria-label="Minimum rating" className="input !w-auto" value={filters.min_rating} onChange={(e) => set("min_rating", e.target.value)}>
              <option value="">Any</option>
              {[5, 6, 7, 8, 9].map((r) => <option key={r} value={r}>★ {r}+</option>)}
            </select>
          </div>
          <div>
            <FieldLabel>Maximum runtime</FieldLabel>
            <select aria-label="Maximum runtime" className="input !w-auto" value={filters.max_runtime} onChange={(e) => set("max_runtime", e.target.value)}>
              <option value="">Any</option>
              {[30, 60, 90, 120, 150, 180].map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
          </div>
        </div>

        {options && options.genres.length > 0 && (
          <div>
            <FieldLabel>Genres (any selected)</FieldLabel>
            <Chips label="Genres" options={options.genres} selected={filters.genres} onChange={(v) => set("genres", v)} />
          </div>
        )}
        {options && options.resolutions.length > 0 && (
          <div>
            <FieldLabel>Resolution (any selected)</FieldLabel>
            <Chips label="Resolution" options={options.resolutions} selected={filters.resolutions} onChange={(v) => set("resolutions", v)} />
          </div>
        )}

        {options && options.languages?.length > 0 && (
          <div>
            <FieldLabel>Original language (any selected)</FieldLabel>
            <Chips label="Original language" options={options.languages} selected={filters.languages} onChange={(v) => set("languages", v)} display={languageName} />
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-gray-700 pt-4">
          <span className="text-sm font-semibold">How many?</span>
          <Segmented label="How many titles" value={filters.count} onChange={(v) => set("count", v)} options={[[10, "10"], [25, "25"], [50, "50"]]} />
          <button className="btn btn-primary" onClick={generate} disabled={loading}>
            <Dices size={16} /> {results ? "Generate new list" : "Generate list"}
          </button>
          {isFiltered && (
            <button className="inline-flex items-center gap-1 text-sm text-indigo-300 hover:underline" onClick={() => setFilters({ ...DEFAULTS, count: filters.count })}>
              <X size={14} /> Clear filters
            </button>
          )}
        </div>
      </section>

      {error && <ErrorBanner message={error} />}

      {results && !loading && (
        <p className="text-sm text-gray-300" role="status">
          {results.items.length === 0
            ? "No titles match these filters. Titles with no value for a filtered field, such as no rating, are left out."
            : results.matches < filters.count && results.matches === results.items.length
              ? `Only ${results.matches.toLocaleString()} ${results.matches === 1 ? "title matches" : "titles match"}, so here ${results.matches === 1 ? "it is" : "they all are"}.`
              : `${results.items.length} random picks from ${results.matches.toLocaleString()} matching titles.`}
        </p>
      )}
      <CardGrid>
        {loading ? <CardPlaceholders count={filters.count} /> : results?.items.map((item) => <LibraryCard key={item.id} item={item} />)}
      </CardGrid>
    </div>
  );
}
