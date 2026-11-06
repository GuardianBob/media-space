import { DatabaseZap, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { post, useApi } from "../api";
import { ErrorBanner, useJob, useJobFinished, useToast } from "../components";

type Mapping = { from: string; to: string };

const MISSING_LABELS = [
  ["poster_url", "No poster"], ["summary", "No summary"], ["trailer_url", "No trailer"], ["country", "No country"],
  ["cast", "No cast"], ["genres", "No genres"], ["release_date", "No date"], ["runtime_minutes", "No runtime"],
];

export function SettingsPage() {
  const { data, error, reload } = useApi("/api/settings");
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [jellyfinKey, setJellyfinKey] = useState("");
  const [tmdbKey, setTmdbKey] = useState("");
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const missing = useApi<Record<string, number>>("/api/library/missing");
  const { job, refresh } = useJob();

  useJobFinished(useCallback((finished) => {
    missing.reload();
    if (finished.name !== "Fill missing data") return;
    if (finished.error) {
      toast(`Filling stopped: ${finished.error}`, true);
    } else {
      toast(`Checked ${finished.result.looked_up} titles and filled in data for ${finished.result.improved}.`);
    }
  }, [missing.reload, toast]));

  const fillMissing = async () => {
    try {
      await post("/api/library/fill-missing");
      refresh();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  useEffect(() => {
    if (data) {
      setUrl(data.jellyfin_url);
      setMappings(data.path_mappings);
    }
  }, [data]);

  const save = async () => {
    try {
      await post("/api/settings", {
        jellyfin_url: url.trim(),
        jellyfin_api_key: jellyfinKey.trim(),
        tmdb_api_key: tmdbKey.trim(),
        path_mappings: mappings.map((m) => ({ from: m.from.trim(), to: m.to.trim() })),
      }, "PUT");
      setJellyfinKey("");
      setTmdbKey("");
      toast("Settings saved.");
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const test = async (service: string) => {
    try {
      const { message } = await post(`/api/settings/test/${service}`);
      toast(message);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const scanJellyfin = async () => {
    try {
      const { message } = await post("/api/settings/jellyfin/scan");
      toast(message);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const updateMapping = (index: number, field: keyof Mapping, value: string) =>
    setMappings((all) => all.map((m, i) => (i === index ? { ...m, [field]: value } : m)));

  return (
    <div className="max-w-3xl space-y-5">
      <h1 className="page-title">Settings</h1>
      {error && <ErrorBanner message={error} />}

      <section className="panel space-y-3">
        <h2 className="font-bold">Jellyfin</h2>
        <label className="block text-sm">
          Server URL
          <input className="input mt-1" placeholder="http://192.168.1.10:8096" value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <label className="block text-sm">
          API key
          <input
            className="input mt-1" type="password" autoComplete="off" value={jellyfinKey}
            placeholder={data?.jellyfin_api_key_set ? "Saved. Leave blank to keep it." : "Not set"}
            onChange={(e) => setJellyfinKey(e.target.value)}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          <button className="btn" onClick={() => test("jellyfin")}>Test saved connection</button>
          <button className="btn" onClick={scanJellyfin}>
            <RefreshCw size={16} /> Scan Jellyfin library
          </button>
        </div>
        <p className="text-sm text-gray-400">
          Tells Jellyfin to look for new and changed files. It needs an administrator's API key and uses the saved connection. Run a sync here after it finishes.
        </p>
      </section>

      <section className="panel space-y-3">
        <h2 className="font-bold">TMDB</h2>
        <label className="block text-sm">
          API key
          <input
            className="input mt-1" type="password" autoComplete="off" value={tmdbKey}
            placeholder={data?.tmdb_api_key_set ? "Saved. Leave blank to keep it." : "Not set"}
            onChange={(e) => setTmdbKey(e.target.value)}
          />
        </label>
        <button className="btn" onClick={() => test("tmdb")}>Test saved key</button>
      </section>

      <section className="panel space-y-3">
        <div>
          <h2 className="font-bold">Path mappings</h2>
          <p className="text-sm text-gray-400">
            Jellyfin reports file paths as its server sees them. Translate them to where the same folders are mounted on this computer, so Open folder can find them.
          </p>
        </div>
        {mappings.map((mapping, index) => (
          <div key={index} className="flex items-center gap-2">
            <input aria-label="Jellyfin path" className="input font-mono" placeholder="/media" value={mapping.from} onChange={(e) => updateMapping(index, "from", e.target.value)} />
            <span aria-hidden className="text-gray-400">→</span>
            <input aria-label="Path on this computer" className="input font-mono" placeholder="/mnt/nas/media" value={mapping.to} onChange={(e) => updateMapping(index, "to", e.target.value)} />
            <button className="btn" aria-label="Remove mapping" onClick={() => setMappings((all) => all.filter((_, i) => i !== index))}><Trash2 size={16} /></button>
          </div>
        ))}
        <button className="btn" onClick={() => setMappings((all) => [...all, { from: "", to: "" }])}><Plus size={16} /> Add mapping</button>
      </section>

      <button className="btn btn-primary" onClick={save}><Save size={16} /> Save settings</button>

      <section className="panel space-y-3">
        <div>
          <h2 className="font-bold">Missing data</h2>
          <p className="text-sm text-gray-400">
            Looks up blank fields on TMDB. Only blanks are filled, so your edits and Jellyfin's values stay as they are. Each sync also does this for new titles.
          </p>
        </div>
        {missing.data && (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
            {MISSING_LABELS.map(([key, label]) => (
              <div key={key} className="flex justify-between gap-2">
                <dt className="text-gray-400">{label}</dt>
                <dd className="font-medium">{(missing.data?.[key] ?? 0).toLocaleString()}</dd>
              </div>
            ))}
          </dl>
        )}
        <button className="btn" onClick={fillMissing} disabled={job?.running}>
          <DatabaseZap size={16} /> Fill missing data from TMDB
        </button>
      </section>
    </div>
  );
}
