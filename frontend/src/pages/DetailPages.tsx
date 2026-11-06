import { Bookmark, Lock, Pencil, PlayCircle, RotateCcw, ShieldCheck, Trash2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { formatBytes, post, useApi, type Location } from "../api";
import { ErrorBanner, HotlinkButton, LibraryBadge, LocationRow, Poster, useToast } from "../components";

interface Credit { name: string; character: string | null; image_url: string | null }

function Hero({ poster, title, year, country, kind, genres, rating, runtime, resolution, badge, summary, trailer, actions }: {
  poster: string | null; title: string; year: number | null; country: string | null; kind: string;
  genres: string[]; rating: number | null; runtime: number | null; resolution?: string | null;
  badge?: ReactNode; summary: string | null; trailer: string | null; actions: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-6 md:flex-row">
      <div className="w-44 shrink-0 self-center overflow-hidden rounded-xl shadow-lg ring-1 ring-gray-700 md:w-56 md:self-start">
        <Poster src={poster} alt={title} />
      </div>
      <div className="min-w-0 flex-1 space-y-3">
        <div className="flex flex-wrap gap-2">{badge}</div>
        <h1 className="text-3xl font-bold">
          {title} {year && <span className="font-normal text-gray-400">({year})</span>}
        </h1>
        <div className="text-sm text-gray-300">
          {[
            kind === "series" ? "Series" : "Movie",
            resolution,
            country,
            runtime && `${runtime} min`,
            rating && `★ ${rating.toFixed(1)}`,
            genres.join(", "),
          ].filter(Boolean).join(" · ")}
        </div>
        <p className="max-w-3xl text-gray-200">{summary || "No summary available."}</p>
        <div className="flex flex-wrap gap-2">
          {actions}
          {trailer && (
            <a className="btn" href={trailer} target="_blank" rel="noreferrer noopener">
              <PlayCircle size={16} /> Watch trailer
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

function Cast({ credits }: { credits: Credit[] }) {
  if (!credits.length) return null;
  return (
    <section>
      <h2 className="mb-3 text-xl font-bold">Cast</h2>
      <div className="flex gap-4 overflow-x-auto pb-2">
        {credits.map((person, i) => (
          <div key={`${person.name}-${i}`} className="w-24 shrink-0 text-center text-xs">
            {person.image_url ? (
              <img src={person.image_url} alt="" loading="lazy" className="mx-auto mb-1 h-20 w-20 rounded-full object-cover" />
            ) : (
              <div className="mx-auto mb-1 flex h-20 w-20 items-center justify-center rounded-full bg-gray-700 text-lg font-bold text-gray-300" aria-hidden>
                {person.name[0]}
              </div>
            )}
            <div className="font-medium">{person.name}</div>
            {person.character && <div className="text-gray-400">{person.character}</div>}
          </div>
        ))}
      </div>
    </section>
  );
}

const EDIT_FIELDS = [
  ["title", "Title"], ["year", "Year"], ["country", "Country"], ["poster_url", "Poster URL"],
  ["trailer_url", "Trailer link"],
] as const;

function EditPanel({ item, onClose, onSaved }: { item: any; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<Record<string, string>>(() =>
    Object.fromEntries([...EDIT_FIELDS.map(([k]) => k), "summary"].map((k) => [k, item[k] == null ? "" : String(item[k])])),
  );
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const patch: Record<string, string | number | null> = {};
    for (const [key, value] of Object.entries(form)) {
      const old = item[key] == null ? "" : String(item[key]);
      if (value === old) continue;
      if (key === "year") {
        if (value && !/^\d{4}$/.test(value)) return setError("Year must be four digits.");
        patch.year = value ? Number(value) : null;
      } else {
        patch[key] = value === "" && key !== "title" ? null : value;
      }
    }
    try {
      await post(`/api/items/${item.id}`, patch, "PATCH");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="panel space-y-3">
      <h2 className="font-bold">Edit</h2>
      {item.on_jellyfin && (
        <p className="text-sm text-gray-300">Changes stay in this app. Fields you change are locked so Jellyfin syncs won't overwrite them.</p>
      )}
      {error && <ErrorBanner message={error} />}
      <div className="grid gap-3 md:grid-cols-2">
        {EDIT_FIELDS.map(([key, label]) => (
          <label key={key} className="text-sm">
            {label}
            <input className="input mt-1" value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
          </label>
        ))}
      </div>
      <label className="block text-sm">
        Summary
        <textarea className="input mt-1" rows={4} value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} />
      </label>
      <div className="flex gap-2">
        <button className="btn btn-primary" onClick={save}>Save</button>
        <button className="btn" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** Verify before Delete: review what goes, then type the title to confirm. */
function DeleteDialog({ itemId, onClose }: { itemId: number; onClose: () => void }) {
  const { data: preview, error: loadError } = useApi(`/api/items/${itemId}/delete-preview`);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const toast = useToast();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const confirm = async () => {
    setBusy(true);
    try {
      await post(`/api/items/${itemId}/delete`, { token: preview.token, confirm_title: typed });
      toast(`Removed “${preview.title}” from the database. No files were changed.`);
      navigate("/");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const remaining = preview ? preview.counts.locations - preview.sample_locations.length : 0;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-labelledby="delete-title">
      <div className="max-h-[90vh] w-full max-w-xl space-y-4 overflow-y-auto rounded-lg border border-gray-600 bg-gray-800 p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <h2 id="delete-title" className="text-lg font-bold">Verify before deleting</h2>
          <button aria-label="Close" onClick={onClose} className="text-gray-400 hover:text-white"><X size={20} /></button>
        </div>
        {loadError && <ErrorBanner message={loadError} />}
        {preview && (
          <>
            <div className="flex gap-2 rounded-md border border-emerald-700 bg-emerald-950/50 p-3 text-sm">
              <ShieldCheck size={18} className="shrink-0 text-emerald-400" />
              <span>Only the database record is removed. <strong>No media files are touched</strong>, on any drive or on Jellyfin.</span>
            </div>
            <div className="text-sm">
              <p className="mb-1 font-semibold">This will remove:</p>
              <ul className="list-disc space-y-0.5 pl-5 text-gray-200">
                <li>The record for <strong>{preview.title}{preview.year ? ` (${preview.year})` : ""}</strong></li>
                {preview.counts.episodes > 0 && <li>{plural(preview.counts.episodes, "episode record")}</li>}
                <li>{plural(preview.counts.locations, "recorded file path")} and {plural(preview.counts.credits, "cast entry", "cast entries")}</li>
                {preview.locked_fields.length > 0 && <li>Your edits to: {preview.locked_fields.join(", ")}</li>}
              </ul>
            </div>
            {preview.will_exclude && (
              <p className="rounded-md border border-amber-600 bg-amber-950/40 p-3 text-sm">
                {preview.on_jellyfin ? "This title is still on Jellyfin. " : ""}
                It will be added to <strong>Excluded</strong> so syncs don't bring it back. You can restore it there{preview.locked_fields.length > 0 ? ", though your edits won't return" : ""}.
              </p>
            )}
            {preview.sample_locations.length > 0 && (
              <details className="text-sm">
                <summary className="font-semibold">Files that stay where they are</summary>
                <ul className="mt-1 space-y-1 font-mono text-xs break-all text-gray-300">
                  {preview.sample_locations.map((l: Location) => (
                    <li key={l.id}>{l.local_path ?? l.stored_path} {formatBytes(l.size_bytes)}</li>
                  ))}
                  {remaining > 0 && <li className="font-sans text-gray-400">…and {remaining.toLocaleString()} more</li>}
                </ul>
              </details>
            )}
            <label className="block text-sm">
              Type <strong className="select-all">{preview.title}</strong> to confirm
              <input autoFocus className="input mt-1" value={typed} onChange={(e) => setTyped(e.target.value)} />
            </label>
            {error && <ErrorBanner message={error} />}
            <div className="flex justify-end gap-2">
              <button className="btn" onClick={onClose}>Cancel</button>
              <button className="btn btn-danger" disabled={busy || typed.trim() !== preview.title.trim()} onClick={confirm}>
                <Trash2 size={16} /> Delete record
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Seasons({ seasons }: { seasons: any[] }) {
  if (!seasons.length) return null;
  const label = (n: number | null) => (n === null ? "Unnumbered" : n === 0 ? "Specials" : `Season ${n}`);
  return (
    <section>
      <h2 className="mb-3 text-xl font-bold">Episodes</h2>
      <div className="space-y-2">
        {seasons.map((season) => (
          <details key={String(season.number)} className="rounded-md border border-gray-700 bg-gray-800/60">
            <summary className="px-4 py-2.5 font-semibold">
              {label(season.number)} <span className="text-sm font-normal text-gray-400">{season.episodes.length} episodes</span>
            </summary>
            <div className="divide-y divide-gray-700 border-t border-gray-700 px-4">
              {season.episodes.map((ep: any) => (
                <div key={ep.id} className="py-2.5">
                  <div className="text-sm font-medium">
                    {ep.episode_number != null && <span className="mr-2 text-gray-400">{ep.episode_number}</span>}
                    {ep.title || "Untitled"}
                    {ep.air_date && <span className="ml-2 text-xs font-normal text-gray-400">{ep.air_date}</span>}
                  </div>
                  {ep.locations.map((l: Location) => <LocationRow key={l.id} location={l} />)}
                </div>
              ))}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

export function ItemPage() {
  const id = Number(useParams().id);
  const { data: item, error, reload } = useApi(`/api/items/${id}`);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const toast = useToast();

  if (error) return <ErrorBanner message={error} />;
  if (!item) return <div className="h-64 animate-pulse rounded-xl bg-gray-800" />;

  const unlock = async (field: string) => {
    try {
      await post(`/api/items/${id}/unlock/${field}`);
      toast(`${field} will follow Jellyfin again from the next sync.`);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const allLocations: Location[] = [
    ...item.locations,
    ...item.seasons.flatMap((s: any) => s.episodes.flatMap((e: any) => e.locations)),
  ];
  const canOpen = allLocations.some((l) => l.available);
  // A movie shows its best version's exact size; a series shows its mix of qualities.
  const best = allLocations.find((l) => l.resolution === item.resolution && l.width);
  const resolutionText = item.kind === "movie"
    ? item.resolution && (best ? `${item.resolution} (${best.width}×${best.height})` : item.resolution)
    : item.resolution_breakdown.map((r: any) => `${r.resolution} × ${r.count.toLocaleString()}`).join(", ");
  const openHint = allLocations.length === 0
    ? "No file is recorded for this title"
    : canOpen ? "Show in file manager" : allLocations[0].reason ?? "File not available";

  return (
    <div className="space-y-8 pt-2">
      <Hero
        poster={item.poster_url}
        title={item.title}
        year={item.year}
        country={item.country}
        kind={item.kind}
        genres={item.genres}
        rating={item.community_rating}
        runtime={item.runtime_minutes}
        resolution={resolutionText}
        summary={item.summary}
        trailer={item.trailer_url}
        badge={<LibraryBadge item={item} />}
        actions={
          <>
            <HotlinkButton
              primary
              url={`/api/items/${id}/open`}
              disabled={!canOpen}
              title={openHint}
            />
            <button className="btn" onClick={() => setEditing(true)}><Pencil size={16} /> Edit</button>
            <button className="btn" onClick={() => setDeleting(true)}><Trash2 size={16} /> Delete</button>
          </>
        }
      />
      {editing && <EditPanel item={item} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
      {item.on_jellyfin && item.locked_fields.length > 0 && (
        <section className="panel text-sm">
          <h2 className="mb-2 flex items-center gap-2 font-bold"><Lock size={16} /> Fields kept from your edits</h2>
          <div className="flex flex-wrap gap-2">
            {item.locked_fields.map((field: string) => (
              <button key={field} className="btn" onClick={() => unlock(field)} title="Use Jellyfin's value from the next sync">
                <RotateCcw size={14} /> {field}: back to Jellyfin
              </button>
            ))}
          </div>
        </section>
      )}
      <Cast credits={item.credits} />
      {item.locations.length > 0 && (
        <section>
          <h2 className="mb-2 text-xl font-bold">Files</h2>
          <div className="divide-y divide-gray-700 rounded-md border border-gray-700 bg-gray-800/60 px-4">
            {item.locations.map((l: Location) => <LocationRow key={l.id} location={l} />)}
          </div>
        </section>
      )}
      <Seasons seasons={item.seasons} />
      {deleting && <DeleteDialog itemId={id} onClose={() => setDeleting(false)} />}
    </div>
  );
}

export function TmdbPage() {
  const { kind = "movie", id } = useParams();
  const { data: item, error } = useApi(`/api/tmdb/${kind}/${id}`);
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();
  const toast = useToast();

  if (error) return <ErrorBanner message={error} />;
  if (!item) return <div className="h-64 animate-pulse rounded-xl bg-gray-800" />;

  const save = async () => {
    setSaving(true);
    try {
      const { item_id } = await post("/api/discover/save", { tmdb_id: item.tmdb_id, kind: item.kind });
      toast(`Saved “${item.title}” to your database, marked as not on Jellyfin.`);
      navigate(`/item/${item_id}`);
    } catch (e) {
      toast((e as Error).message, true);
      setSaving(false);
    }
  };

  return (
    <div className="space-y-8 pt-2">
      <Hero
        poster={item.poster_url}
        title={item.title}
        year={item.year}
        country={item.country}
        kind={item.kind}
        genres={item.genres}
        rating={item.community_rating}
        runtime={item.runtime_minutes}
        summary={item.summary}
        trailer={item.trailer_url}
        actions={
          item.item_id ? (
            <Link className="btn btn-primary" to={`/item/${item.item_id}`}>View in library</Link>
          ) : item.status === "excluded" ? (
            <Link className="btn" to="/excluded">Excluded: manage</Link>
          ) : (
            <button className="btn btn-primary" onClick={save} disabled={saving}>
              <Bookmark size={16} /> Save to database
            </button>
          )
        }
      />
      <Cast credits={item.credits} />
    </div>
  );
}

