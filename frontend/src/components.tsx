import { CircleSlash, FolderOpen, HardDrive, ImageOff, PenLine, Server, Tv } from "lucide-react";
import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import { api, formatBytes, post, type Card, type JobStatus, type Location, type TmdbCard } from "./api";

// ---- Toasts --------------------------------------------------------------------------

type Toast = { id: number; text: string; error: boolean };
const ToastContext = createContext<(text: string, error?: boolean) => void>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);
  const add = useCallback((text: string, error = false) => {
    const id = nextId.current++;
    setToasts((all) => [...all, { id, text, error }]);
    setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), error ? 9000 : 4000);
  }, []);
  return (
    <ToastContext.Provider value={add}>
      {children}
      <div role="status" className="fixed right-4 bottom-4 z-50 flex max-w-md flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded-md border px-4 py-3 text-sm shadow-lg ${
              t.error ? "border-red-500 bg-red-950" : "border-gray-600 bg-gray-800"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ---- Background job (sync / scan) ----------------------------------------------------

const JobContext = createContext<{ job: JobStatus | null; finished: number; refresh: () => void }>({
  job: null,
  finished: 0,
  refresh: () => {},
});
export const useJob = () => useContext(JobContext);

export function JobProvider({ children }: { children: ReactNode }) {
  const [job, setJob] = useState<JobStatus | null>(null);
  const [finished, setFinished] = useState(0);
  const wasRunning = useRef(false);

  const refresh = useCallback(() => {
    api<JobStatus>("/api/jobs")
      .then((status) => {
        if (wasRunning.current && !status.running) setFinished((n) => n + 1);
        wasRunning.current = status.running;
        setJob(status);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [refresh]);

  return <JobContext.Provider value={{ job, finished, refresh }}>{children}</JobContext.Provider>;
}

/** Calls `onDone` with each job's final status. */
export function useJobFinished(onDone: (job: JobStatus) => void) {
  const { job, finished } = useJob();
  const seen = useRef(finished);
  useEffect(() => {
    if (finished !== seen.current && job) {
      seen.current = finished;
      onDone(job);
    }
  }, [finished, job, onDone]);
}

// ---- Status badges -------------------------------------------------------------------

const BADGE_STYLES = {
  jellyfin: "bg-violet-600/90 text-white",
  manual: "bg-amber-500 text-gray-950",
  removed: "bg-red-700 text-white",
  series: "bg-gray-900/80 text-gray-100",
};

function Badge({ tone, icon, label, compact }: {
  tone: keyof typeof BADGE_STYLES; icon: ReactNode; label: string; compact?: boolean;
}) {
  return (
    <span
      title={label}
      aria-label={label}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${BADGE_STYLES[tone]}`}
    >
      {icon}
      {!compact && label}
    </span>
  );
}

export function LibraryBadge({ item, compact }: {
  item: Pick<Card, "on_jellyfin" | "removed_from_jellyfin" | "source">; compact?: boolean;
}) {
  if (item.on_jellyfin) return <Badge tone="jellyfin" icon={<Server size={12} />} label="On Jellyfin" compact={compact} />;
  if (item.removed_from_jellyfin) return <Badge tone="removed" icon={<CircleSlash size={12} />} label="Removed from Jellyfin" compact={compact} />;
  const icon = item.source === "scan" ? <HardDrive size={12} /> : <PenLine size={12} />;
  const origin = { scan: "from drive", csv: "from CSV", tmdb: "from TMDB" }[item.source] ?? "";
  return <Badge tone="manual" icon={icon} label={`Not on Jellyfin (${origin})`} compact={compact} />;
}

// ---- Poster cards --------------------------------------------------------------------

export function Poster({ src, alt }: { src: string | null; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div className="flex aspect-[2/3] w-full flex-col items-center justify-center gap-2 bg-gray-800 p-2 text-center text-xs text-gray-400">
        <ImageOff size={24} />
        {alt}
      </div>
    );
  }
  return <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} className="aspect-[2/3] w-full object-cover" />;
}

function CardFrame({ to, poster, title, year, summary, topLeft, topRight, footer }: {
  to: string; poster: string | null; title: string; year: number | null; summary?: string;
  topLeft?: ReactNode; topRight?: ReactNode; footer?: ReactNode;
}) {
  return (
    <div className="group relative overflow-hidden rounded-xl bg-gray-800 shadow ring-1 ring-gray-700 transition hover:scale-[1.03] hover:ring-gray-500 focus-within:ring-indigo-400">
      <Link to={to} className="block" aria-label={`${title}${year ? ` (${year})` : ""}`}>
        <Poster src={poster} alt={title} />
        <div className="absolute inset-0 flex flex-col justify-end bg-gradient-to-t from-gray-950 via-gray-950/70 to-transparent p-3 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100">
          {year && <div className="text-xs text-gray-300">{year}</div>}
          <div className="text-sm leading-tight font-bold text-white">{title}</div>
          {summary && <div className="mt-1 line-clamp-4 text-xs text-gray-300">{summary}</div>}
        </div>
      </Link>
      <div className="pointer-events-none absolute top-2 left-2">{topLeft}</div>
      <div className="pointer-events-none absolute top-2 right-2">{topRight}</div>
      {footer && <div className="absolute inset-x-2 bottom-2 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100">{footer}</div>}
    </div>
  );
}

const KindBadge = ({ kind }: { kind: string }) =>
  kind === "series" ? <Badge tone="series" icon={<Tv size={12} />} label="Series" compact /> : null;

export function LibraryCard({ item }: { item: Card }) {
  return (
    <CardFrame
      to={`/item/${item.id}`}
      poster={item.poster_url}
      title={item.title}
      year={item.year}
      topLeft={
        <div className="flex items-center gap-1">
          <KindBadge kind={item.kind} />
          {item.resolution && (
            <span title={`Resolution: ${item.resolution}`} className="rounded-full bg-gray-900/85 px-2 py-0.5 text-xs font-semibold text-gray-100">
              {item.resolution}
            </span>
          )}
        </div>
      }
      topRight={<LibraryBadge item={item} compact />}
    />
  );
}

export function DiscoverCard({ item }: { item: TmdbCard }) {
  const to = item.item_id ? `/item/${item.item_id}` : `/tmdb/${item.kind}/${item.tmdb_id}`;
  const status =
    item.status === "jellyfin" ? <Badge tone="jellyfin" icon={<Server size={12} />} label="On Jellyfin" compact />
    : item.status === "saved" ? <Badge tone="manual" icon={<PenLine size={12} />} label="Saved, not on Jellyfin" compact />
    : item.status === "excluded" ? <Badge tone="removed" icon={<CircleSlash size={12} />} label="Excluded" compact />
    : null;
  return (
    <CardFrame
      to={to}
      poster={item.poster_url}
      title={item.title}
      year={item.year}
      summary={item.summary}
      topLeft={<KindBadge kind={item.kind} />}
      topRight={status}
    />
  );
}

export const CardGrid = ({ children }: { children: ReactNode }) => (
  <div className="grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-4">{children}</div>
);

export function CardPlaceholders({ count = 12 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="aspect-[2/3] animate-pulse rounded-xl bg-gray-800" />
      ))}
    </>
  );
}

/** Fires `onVisible` when scrolled into view: the trigger for infinite scroll. */
export function ScrollSentinel({ onVisible, enabled }: { onVisible: () => void; enabled: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!enabled || !ref.current) return;
    const observer = new IntersectionObserver(
      (entries) => entries[0].isIntersecting && onVisible(),
      { rootMargin: "600px" },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [enabled, onVisible]);
  return <div ref={ref} className="h-1" />;
}

export const ErrorBanner = ({ message, action }: { message: string; action?: ReactNode }) => (
  <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-600 bg-red-950/60 px-4 py-3 text-sm">
    <span>{message}</span>
    {action}
  </div>
);

// ---- Hotlink -------------------------------------------------------------------------

export function HotlinkButton({ url, label = "Open folder", disabled, title, primary }: {
  url: string; label?: string; disabled?: boolean; title?: string; primary?: boolean;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const open = async () => {
    setBusy(true);
    try {
      const { opened } = await post<{ opened: string }>(url);
      toast(`Opened ${opened}`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className={`btn ${primary ? "btn-primary" : ""}`} onClick={open} disabled={busy || disabled} title={title}>
      <FolderOpen size={16} />
      {label}
    </button>
  );
}

export function LocationRow({ location }: { location: Location }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-sm">
      <div className="min-w-0 flex-1">
        <div className={`font-mono text-xs break-all ${location.available ? "text-gray-200" : "text-gray-400"}`}>
          {location.local_path ?? location.stored_path}
        </div>
        {(location.resolution || location.size_bytes) && (
          <div className="mt-0.5 text-xs text-gray-300">
            {[
              location.resolution,
              location.width && location.height && `${location.width}×${location.height}`,
              formatBytes(location.size_bytes),
            ].filter(Boolean).join(" · ")}
          </div>
        )}
        {!location.available && <div className="mt-0.5 text-xs text-amber-400">{location.reason}</div>}
      </div>
      <HotlinkButton
        url={`/api/locations/${location.id}/open`}
        label="Open"
        disabled={!location.available}
        title={location.reason ?? "Show in file manager"}
      />
    </div>
  );
}
