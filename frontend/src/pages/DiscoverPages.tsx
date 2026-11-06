import { ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, useApi, type Card, type TmdbCard } from "../api";
import { CardGrid, CardPlaceholders, DiscoverCard, ErrorBanner, LibraryCard, ScrollSentinel } from "../components";

const LISTS: Record<string, { title: string; url: string; genreKind?: string }> = {
  trending: { title: "Trending", url: "/api/discover/trending" },
  movies: { title: "Popular Movies", url: "/api/discover/movie", genreKind: "movie" },
  series: { title: "Popular Series", url: "/api/discover/series", genreKind: "series" },
  upcoming: { title: "Upcoming Movies", url: "/api/discover/upcoming" },
};

function KeyHint({ message }: { message: string }) {
  return (
    <ErrorBanner
      message={message}
      action={<Link to="/settings" className="btn">Open Settings</Link>}
    />
  );
}

function Slider({ listKey }: { listKey: string }) {
  const list = LISTS[listKey];
  const { data, error, loading } = useApi<{ results: TmdbCard[] }>(list.url);
  if (error) return <KeyHint message={`${list.title}: ${error}`} />;
  return (
    <section>
      <Link to={`/discover/list/${listKey}`} className="mb-3 inline-flex items-center gap-1 text-xl font-bold hover:text-indigo-300">
        {list.title} <ChevronRight size={20} />
      </Link>
      <div className="flex gap-4 overflow-x-auto pb-3">
        {loading && Array.from({ length: 8 }, (_, i) => <div key={i} className="aspect-[2/3] w-36 shrink-0 animate-pulse rounded-xl bg-gray-800 md:w-44" />)}
        {data?.results.map((item) => (
          <div key={`${item.kind}-${item.tmdb_id}`} className="w-36 shrink-0 md:w-44">
            <DiscoverCard item={item} />
          </div>
        ))}
      </div>
    </section>
  );
}

export function DiscoverPage() {
  return (
    <div className="space-y-6">
      <h1 className="page-title">Discover</h1>
      {Object.keys(LISTS).map((key) => <Slider key={key} listKey={key} />)}
    </div>
  );
}

function useTmdbPages(baseUrl: string | null) {
  const [results, setResults] = useState<TmdbCard[]>([]);
  const [totalPages, setTotalPages] = useState(0);
  const [loading, setLoading] = useState(baseUrl !== null);
  const [error, setError] = useState<string | null>(null);
  const page = useRef(0);
  const request = useRef(0);

  const load = useCallback(async (next: number) => {
    if (!baseUrl) return;
    const id = ++request.current;
    setLoading(true);
    try {
      const joiner = baseUrl.includes("?") ? "&" : "?";
      const data = await api(`${baseUrl}${joiner}page=${next}`);
      if (id !== request.current) return;
      setResults((current) => {
        if (next === 1) return data.results;
        // TMDB can repeat a title across pages while rankings shift.
        const seen = new Set(current.map((r) => `${r.kind}-${r.tmdb_id}`));
        return [...current, ...data.results.filter((r: TmdbCard) => !seen.has(`${r.kind}-${r.tmdb_id}`))];
      });
      setTotalPages(data.total_pages);
      page.current = next;
      setError(null);
    } catch (e) {
      if (id === request.current) setError((e as Error).message);
    } finally {
      if (id === request.current) setLoading(false);
    }
  }, [baseUrl]);

  useEffect(() => {
    setResults([]);
    load(1);
  }, [load]);

  const loadMore = useCallback(() => load(page.current + 1), [load]);
  return { results, loading, error, loadMore, hasMore: page.current < totalPages };
}

export function DiscoverList() {
  const { list: listKey = "" } = useParams();
  const list = LISTS[listKey];
  const [genre, setGenre] = useState("");
  const { data: genres } = useApi<{ id: number; name: string }[]>(
    list?.genreKind ? `/api/discover/genres/${list.genreKind}` : null,
  );
  const url = list ? (genre ? `${list.url}?genre=${genre}` : list.url) : null;
  const { results, loading, error, loadMore, hasMore } = useTmdbPages(url);
  if (!list) return <ErrorBanner message="Unknown list." />;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="page-title">{list.title}</h1>
        {genres && (
          <select aria-label="Genre" className="input !w-auto" value={genre} onChange={(e) => setGenre(e.target.value)}>
            <option value="">All genres</option>
            {genres.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        )}
      </div>
      {error && <KeyHint message={error} />}
      <CardGrid>
        {results.map((item) => <DiscoverCard key={`${item.kind}-${item.tmdb_id}`} item={item} />)}
        {loading && <CardPlaceholders />}
      </CardGrid>
      <ScrollSentinel onVisible={loadMore} enabled={hasMore && !loading && !error} />
    </div>
  );
}

export function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const library = useApi<{ items: Card[]; total: number }>(q ? `/api/items?q=${encodeURIComponent(q)}&size=100` : null);
  const tmdb = useTmdbPages(q ? `/api/discover/search?q=${encodeURIComponent(q)}` : null);
  // Already shown under "In your library", so only titles you don't have are listed.
  const unowned = tmdb.results.filter((r) => !r.item_id);

  return (
    <div className="space-y-8">
      <h1 className="page-title">Results for “{q}”</h1>
      <section className="space-y-3">
        <h2 className="text-xl font-bold">
          In your library {library.data && <span className="text-sm font-normal text-gray-400">{library.data.total}</span>}
        </h2>
        {library.error && <ErrorBanner message={library.error} />}
        {library.data?.total === 0 && <p className="text-gray-400">Nothing in your library matches.</p>}
        <CardGrid>
          {library.data?.items.map((item) => <LibraryCard key={item.id} item={item} />)}
          {library.loading && <CardPlaceholders count={6} />}
        </CardGrid>
      </section>
      <section className="space-y-3">
        <h2 className="text-xl font-bold">Discover on TMDB</h2>
        {tmdb.error && <KeyHint message={tmdb.error} />}
        {!tmdb.loading && !tmdb.error && unowned.length === 0 && <p className="text-gray-400">No other titles found.</p>}
        <CardGrid>
          {unowned.map((item) => <DiscoverCard key={`${item.kind}-${item.tmdb_id}`} item={item} />)}
          {tmdb.loading && <CardPlaceholders count={6} />}
        </CardGrid>
        <ScrollSentinel onVisible={tmdb.loadMore} enabled={tmdb.hasMore && !tmdb.loading && !tmdb.error} />
      </section>
    </div>
  );
}
