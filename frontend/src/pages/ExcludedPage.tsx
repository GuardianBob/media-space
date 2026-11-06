import { Undo2 } from "lucide-react";
import { post, useApi } from "../api";
import { ErrorBanner, useToast } from "../components";

export function ExcludedPage() {
  const { data, error, loading, reload } = useApi<any[]>("/api/excluded");
  const toast = useToast();

  const restore = async (row: any) => {
    try {
      await post(`/api/excluded/${row.id}`, undefined, "DELETE");
      toast(`“${row.title}” will come back on the next Jellyfin sync.`);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">Excluded</h1>
        <p className="text-sm text-gray-400">Titles you deleted that are also on Jellyfin. Syncs skip them until you restore them.</p>
      </div>
      {error && <ErrorBanner message={error} />}
      {!loading && data?.length === 0 && <p className="py-10 text-center text-gray-400">Nothing is excluded.</p>}
      {data?.map((row) => (
        <div key={row.id} className="panel flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="font-medium">{row.title} {row.year && <span className="text-gray-400">({row.year})</span>}</div>
            <div className="text-xs text-gray-400">Excluded {new Date(row.excluded_at).toLocaleString()}</div>
          </div>
          <button className="btn" onClick={() => restore(row)}><Undo2 size={16} /> Restore</button>
        </div>
      ))}
    </div>
  );
}
