import { useCallback, useEffect, useState } from "react";
import { Loader2, RotateCw, X } from "lucide-react";
import {
  getAdminOverview,
  getIngestionFailures,
  reprocessIngestionFailure,
  dismissIngestionFailure,
  type AdminOverview,
  type IngestionFailure,
} from "../lib/api";
import { Button } from "./ui/button";

export function Admin() {
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [failures, setFailures] = useState<IngestionFailure[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getAdminOverview().then(setOverview).catch((e) => setError((e as Error).message));
    getIngestionFailures().then(setFailures).catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => { load(); }, [load]);

  const reprocess = async (id: string) => {
    setBusyId(id);
    try {
      await reprocessIngestionFailure(id);
    } catch {
      // Reprocess failing again (e.g. still-unsupported format) is expected —
      // the failure list reload below shows the updated attempt count/error.
    }
    load();
    setBusyId(null);
  };

  const dismiss = async (id: string) => {
    setBusyId(id);
    try {
      await dismissIngestionFailure(id);
    } finally {
      load();
      setBusyId(null);
    }
  };

  if (error) return <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">{error}</div>;
  if (!overview) return <div className="grid place-items-center py-8"><Loader2 className="size-5 animate-spin text-primary" /></div>;

  const { documents, usage, pipeline } = overview;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Documents</span>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Total" value={documents.total_documents} />
          <Stat label="Indexed" value={pipeline.documents_indexed} />
          <Stat label="Duplicates" value={pipeline.documents_duplicate} />
          <Stat label="Recent errors" value={pipeline.upload_errors_recent} />
        </div>
        {documents.by_type.length > 0 && (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {documents.by_type.map((t) => (
              <li key={t.document_type} className="flex items-center justify-between px-3 py-2 text-sm">
                <span className="font-mono text-xs">{t.document_type}</span>
                <span className="font-mono text-xs tabular-nums text-primary">{t.count}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="font-mono text-[11px] text-muted-foreground">{pipeline.note}</p>
      </div>

      <div className="flex flex-col gap-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          Ingestion failures — {failures?.length ?? 0} pending
        </span>
        {!failures || failures.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing awaiting reprocessing.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {failures.map((f) => (
              <li key={f.id} className="flex flex-col gap-2 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-mono text-xs">{f.filename}</span>
                  <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                    {f.attempts} attempt{f.attempts === 1 ? "" : "s"}
                  </span>
                  <div className="ml-auto flex shrink-0 items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busyId === f.id}
                      onClick={() => reprocess(f.id)}
                    >
                      {busyId === f.id ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCw className="size-3.5" />}
                      Reprocess
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busyId === f.id}
                      onClick={() => dismiss(f.id)}
                      aria-label={`Dismiss ${f.filename}`}
                    >
                      <X className="size-3.5" />
                    </Button>
                  </div>
                </div>
                <p className="text-xs text-destructive/90">{f.error_message}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Request usage</span>
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Total requests" value={usage.total_requests} />
          <Stat label="LLM-invoking" value={usage.llm_invoking_requests} />
          <Stat label="Errors" value={usage.total_errors} />
        </div>
        {usage.by_endpoint.length === 0 ? (
          <p className="text-sm text-muted-foreground">No requests recorded yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {usage.by_endpoint.map((e) => (
              <li key={e.path} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="truncate font-mono text-xs">{e.path}</span>
                <div className="flex shrink-0 items-center gap-3 font-mono text-xs tabular-nums text-muted-foreground">
                  <span>{e.avg_duration_ms}ms</span>
                  {e.error_count > 0 && <span className="text-destructive">{e.error_count} err</span>}
                  <span className="text-primary">{e.request_count}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="font-mono text-[11px] text-muted-foreground">{usage.note}</p>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border p-3">
      <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="text-2xl font-bold tabular-nums">{value}</span>
    </div>
  );
}
