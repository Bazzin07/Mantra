import { useRef, useState } from "react";
import { Upload, FileText, Loader2, AlertCircle } from "lucide-react";
import { uploadDoc, type Doc, type Entity } from "../lib/api";
import { cn } from "../lib/utils";

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
