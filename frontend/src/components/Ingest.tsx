import { useRef, useState } from "react";
import { Upload, FileText, Loader2, AlertCircle, Sparkles, Download } from "lucide-react";
import { uploadDoc, type Doc, type Entity } from "../lib/api";
import { cn } from "../lib/utils";

// Ready-made cross-referenced demo documents (a P-205B pump / K-330 compressor
// story) served from the app's own /samples folder. One click ingests them
// through the exact same upload flow as a judge's own file — nothing is
// pre-seeded, the entities/graph/RCA/compliance/failures all come from these.
const SAMPLES = [
  { file: "sample_1_work_order_P-205B.pdf", label: "Work order — Pump P-205B", note: "seal failure, OISD-154" },
  { file: "sample_2_inspection_K-330.pdf", label: "Inspection — Compressor K-330", note: "vibration, PESO" },
  { file: "sample_3_incident_INC-2026-050.pdf", label: "Incident — INC-2026-050", note: "links P-205B ↔ K-330" },
];

function EntityChip({ e }: { e: Entity }) {
  const isTag = e.entity_type === "EQUIPMENT_TAG";
  return (
    <span
      className={cn(
        "rounded-sm border px-1.5 py-0.5 font-mono text-[11px]",
        isTag ? "border-primary/40 text-primary" : "border-border text-muted-foreground",
      )}
      title={e.entity_type}
    >
      {e.text}
    </span>
  );
}

export function Ingest() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function handle(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    for (const file of Array.from(files)) {
      try {
        const doc = await uploadDoc(file);
        setDocs((d) => [doc, ...d.filter((x) => x.id !== doc.id)]);
      } catch (err) {
        setError(`${file.name}: ${(err as Error).message}`);
      }
    }
    setBusy(false);
  }

  // Fetch a bundled sample PDF and run it through the real upload flow, so the
  // result a judge sees is identical to uploading their own document.
  async function loadSamples(files: string[]) {
    setBusy(true);
    setError(null);
    for (const name of files) {
      try {
        const res = await fetch(`/samples/${name}`);
        if (!res.ok) throw new Error(`could not load ${name}`);
        const blob = await res.blob();
        const doc = await uploadDoc(new File([blob], name, { type: "application/pdf" }));
        setDocs((d) => [doc, ...d.filter((x) => x.id !== doc.id)]);
      } catch (err) {
        setError(`${name}: ${(err as Error).message}`);
      }
    }
    setBusy(false);
  }

  return (
    <div className="flex flex-col gap-6">
      <label
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); handle(e.dataTransfer.files); }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed py-14 text-center transition-colors",
          drag ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/50",
        )}
      >
        <input
          ref={input} type="file" multiple className="sr-only"
          accept=".txt,.md,.csv,.eml,.pdf,.docx,.pptx,.xlsx,.png,.jpg,.jpeg"
          onChange={(e) => handle(e.target.files)}
        />
        {busy ? (
          <Loader2 className="size-6 animate-spin text-primary" />
        ) : (
          <Upload className="size-6 text-muted-foreground" />
        )}
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium">
            {busy ? "Processing…" : "Drop documents or click to upload"}
          </span>
          <span className="text-xs text-muted-foreground">
            PDF · DOCX · XLSX · PPTX · EML · images · text
          </span>
        </div>
      </label>

      <div className="flex flex-col gap-3 rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Sparkles className="size-4 text-primary" />
          <span className="text-sm font-medium">Try it with sample documents</span>
          <button
            onClick={() => loadSamples(SAMPLES.map((s) => s.file))}
            disabled={busy}
            className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
            Ingest all 3
          </button>
        </div>
        <p className="text-xs text-muted-foreground">
          A cross-referenced maintenance story. Ingest them, then explore every tab: ask
          <span className="font-mono text-foreground"> "What happened to P-205B?"</span>, run RCA on
          <span className="font-mono text-foreground"> K-330</span>, and check Compliance & Failures.
        </p>
        <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
          {SAMPLES.map((s) => (
            <li key={s.file} className="flex items-center gap-2 px-3 py-2">
              <FileText className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="text-sm">{s.label}</span>
              <span className="font-mono text-[11px] text-muted-foreground">{s.note}</span>
              <div className="ml-auto flex shrink-0 items-center gap-1">
                <button
                  onClick={() => loadSamples([s.file])}
                  disabled={busy}
                  className="rounded px-2 py-1 text-xs text-primary hover:bg-muted disabled:opacity-50"
                >
                  Ingest
                </button>
                <a
                  href={`/samples/${s.file}`}
                  download
                  className="grid size-7 place-items-center rounded text-muted-foreground hover:bg-muted"
                  title="Download PDF"
                >
                  <Download className="size-3.5" />
                </a>
              </div>
            </li>
          ))}
        </ul>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">
          <AlertCircle className="size-4 shrink-0" /> {error}
        </div>
      )}

      {docs.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No documents yet. Uploads are parsed, chunked, and mined for equipment tags,
          procedures, regulations, people, parts, and failure modes.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {docs.map((doc) => (
            <li key={doc.id} className="flex flex-col gap-2 py-4 animate-fade-up">
              <div className="flex items-center gap-2">
                <FileText className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate text-sm font-medium">{doc.metadata.filename}</span>
                <span
                  className={cn(
                    "ml-auto shrink-0 rounded-sm px-1.5 py-0.5 font-mono text-[10px] uppercase",
                    doc.status === "duplicate" ? "bg-muted text-muted-foreground" : "bg-primary/15 text-primary",
                  )}
                >
                  {doc.status}
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5 pl-6">
                {doc.entities.length ? (
                  doc.entities.map((e, i) => <EntityChip key={i} e={e} />)
                ) : (
                  <span className="text-xs text-muted-foreground">no entities extracted</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
