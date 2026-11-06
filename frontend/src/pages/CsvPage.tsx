import { Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { api, post } from "../api";
import { ErrorBanner, useToast } from "../components";

interface Candidate { id: number; kind: string; title: string; year: number | null; changes: Record<string, unknown> }
interface Row {
  line: number; title: string; year: number | null; country: string | null; kind: string;
  status: "update" | "unchanged" | "ambiguous" | "unmatched" | "invalid";
  item_id: number | null; changes: Record<string, unknown>; candidates: Candidate[]; message?: string;
}
// "skip", "create", "update" (the matched record), or the ID of an ambiguous row's candidate.
type Choice = string;

const STATUS_LABEL: Record<Row["status"], string> = {
  update: "Update", unchanged: "No change", ambiguous: "Needs a choice", unmatched: "No match", invalid: "Invalid",
};
const STATUS_STYLE: Record<Row["status"], string> = {
  update: "bg-indigo-700", unchanged: "bg-gray-600", ambiguous: "bg-amber-500 text-gray-950", unmatched: "bg-sky-800", invalid: "bg-red-700",
};

const describe = (changes: Record<string, unknown>) =>
  Object.entries(changes).map(([field, value]) => `${field} → ${value}`).join(", ");

export function CsvPage() {
  const toast = useToast();
  const [fileName, setFileName] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [choices, setChoices] = useState<Record<number, Choice>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    setRows(null);
    setFileName(file.name);
    try {
      const body = await file.arrayBuffer();
      const preview = await api<{ rows: Row[] }>("/api/csv/preview", {
        method: "POST",
        headers: { "Content-Type": "text/csv" },
        body,
      });
      setRows(preview.rows);
      // Updates are ticked by default. New records and ambiguous rows wait for a decision.
      setChoices(Object.fromEntries(preview.rows.map((r) => [r.line, r.status === "update" ? "update" : "skip"])));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const decisions = useMemo(() => {
    return (rows ?? []).flatMap((row) => {
      const choice = choices[row.line];
      const values = { title: row.title, year: row.year, country: row.country, kind: row.kind };
      if (choice === "create") return [{ action: "create", ...values }];
      if (choice === "update") return [{ action: "update", item_id: row.item_id, ...values }];
      if (choice && choice !== "skip") return [{ action: "update", item_id: Number(choice), ...values }];
      return [];
    });
  }, [rows, choices]);

  const apply = async () => {
    setBusy(true);
    try {
      const result = await post("/api/csv/apply", decisions);
      toast(`Import finished: ${result.updated} updated, ${result.created} created.`);
      setRows(null);
      setFileName("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const counts = (rows ?? []).reduce<Record<string, number>>((all, r) => ({ ...all, [r.status]: (all[r.status] ?? 0) + 1 }), {});
  const setChoice = (line: number, value: Choice) => setChoices((all) => ({ ...all, [line]: value }));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="page-title">CSV Import</h1>
        <p className="text-sm text-gray-400">
          Columns: <code>Title</code>, <code>Year</code>, <code>Country</code> (optional <code>Type</code>: movie or series).
          Nothing changes until you apply. Blank cells never erase a value, and imported values are kept through Jellyfin syncs.
        </p>
      </div>

      <label className="btn btn-primary w-fit">
        <Upload size={16} /> {busy ? "Working…" : "Choose CSV file"}
        <input
          type="file"
          accept=".csv,.tsv,.txt,text/csv"
          className="sr-only"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
      </label>

      {error && <ErrorBanner message={error} />}

      {rows && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-semibold">{fileName}</span>
              {Object.entries(counts).map(([status, count]) => (
                <span key={status} className={`rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_STYLE[status as Row["status"]]}`}>
                  {count} {STATUS_LABEL[status as Row["status"]]}
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <button className="btn" onClick={() => setRows(null)}>Cancel</button>
              <button className="btn btn-primary" disabled={busy || decisions.length === 0} onClick={apply}>
                Apply {decisions.length} {decisions.length === 1 ? "row" : "rows"}
              </button>
            </div>
          </div>
          <div className="overflow-x-auto rounded-md border border-gray-700">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-800 text-xs text-gray-400 uppercase">
                <tr>
                  <th className="px-3 py-2">Line</th><th className="px-3 py-2">Title</th><th className="px-3 py-2">Year</th>
                  <th className="px-3 py-2">Country</th><th className="px-3 py-2">Result</th><th className="px-3 py-2">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {rows.map((row) => (
                  <tr key={row.line} className={row.status === "unchanged" ? "text-gray-400" : ""}>
                    <td className="px-3 py-2">{row.line}</td>
                    <td className="px-3 py-2 font-medium">{row.title || <em className="text-gray-400">blank</em>}</td>
                    <td className="px-3 py-2">{row.year}</td>
                    <td className="px-3 py-2">{row.country}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_STYLE[row.status]}`}>{STATUS_LABEL[row.status]}</span>
                      {row.message && <span className="ml-2 text-xs text-red-300">{row.message}</span>}
                      {row.status === "update" && <div className="mt-1 text-xs text-gray-300">{describe(row.changes)}</div>}
                    </td>
                    <td className="px-3 py-2">
                      {row.status === "update" && (
                        <label className="flex items-center gap-2">
                          <input type="checkbox" checked={choices[row.line] === "update"} onChange={(e) => setChoice(row.line, e.target.checked ? "update" : "skip")} />
                          Apply
                        </label>
                      )}
                      {row.status === "unmatched" && (
                        <label className="flex items-center gap-2">
                          <input type="checkbox" checked={choices[row.line] === "create"} onChange={(e) => setChoice(row.line, e.target.checked ? "create" : "skip")} />
                          Create as {row.kind}
                        </label>
                      )}
                      {row.status === "ambiguous" && (
                        <select aria-label={`Choice for line ${row.line}`} className="input" value={choices[row.line]} onChange={(e) => setChoice(row.line, e.target.value)}>
                          <option value="skip">Skip</option>
                          {row.candidates.map((c) => (
                            <option key={c.id} value={String(c.id)}>
                              Update {c.title} ({c.year ?? "no year"}, {c.kind}){Object.keys(c.changes).length ? `: ${describe(c.changes)}` : ""}
                            </option>
                          ))}
                          <option value="create">Create new {row.kind}</option>
                        </select>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
