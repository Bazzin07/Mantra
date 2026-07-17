import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { Loader2, Network, Send, X } from "lucide-react";
import { getGraph, type Graph as GraphData } from "../lib/api";
import { useCopilot } from "../hooks/useCopilot";
import { AnswerView } from "./AnswerView";
import { Button } from "./ui/button";

type GraphNode = GraphData["nodes"][number];
type GraphEdge = GraphData["edges"][number];
type Selected = { kind: "node"; node: GraphNode } | { kind: "edge"; edge: GraphEdge } | null;

// Hover tooltips render outside React's tree (force-graph injects raw HTML into
// a floating div), so styling goes through CSS var() references rather than
// Tailwind classes — the vars resolve against light/dark automatically.
const TOOLTIP_STYLE =
  "background:var(--popover);color:var(--popover-foreground);border:1px solid var(--border);" +
  "border-radius:6px;padding:5px 9px;font-family:var(--font-mono),monospace;font-size:11px;";

// Colors come from CSS tokens (--graph-*) so nothing is hardcoded here; three.js
// needs plain hex, which is why those tokens are hex rather than oklch.
function readColors() {
  const s = getComputedStyle(document.documentElement);
  const get = (n: string) => s.getPropertyValue(n).trim();
  return {
    equipment: get("--graph-equipment"),
    document: get("--graph-document"),
    entity: get("--graph-entity"),
    link: get("--graph-link"),
  };
}

export function Graph({ dark }: { dark: boolean }) {
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [colors, setColors] = useState(readColors);
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 640, h: 520 });
  const chat = useCopilot();
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Selected>(null);

  useEffect(() => { getGraph().then(setData).catch((e) => setError((e as Error).message)); }, []);
  useEffect(() => { setColors(readColors()); }, [dark]);

  useEffect(() => {
    if (!wrap.current) return;
    const measure = () => wrap.current && setSize({ w: wrap.current.clientWidth, h: wrap.current.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, [data]);

  const graph = useMemo(
    () =>
      data && {
        nodes: data.nodes.map((n) => ({ id: n.id, name: n.label, type: n.type })),
        links: data.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, type: e.type })),
      },
    [data],
  );

  // force-graph strips custom fields off render nodes/links on interaction; look
  // the full record (with metadata) back up by id instead of trusting the payload.
  const nodesById = useMemo(() => new Map(data?.nodes.map((n) => [n.id, n]) ?? []), [data]);
  const edgesById = useMemo(() => new Map(data?.edges.map((e) => [e.id, e]) ?? []), [data]);

  const nodeColor = (n: { type: string }) =>
    n.type === "EQUIPMENT_TAG" ? colors.equipment : n.type === "DOCUMENT" ? colors.document : colors.entity;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Network className="size-3.5" />
        {data ? `${data.nodes.length} nodes · ${data.edges.length} edges` : "Loading graph…"}
        <span className="ml-auto flex items-center gap-3 font-mono text-[10px]">
          <Legend color={colors.equipment} label="equipment" />
          <Legend color={colors.document} label="document" />
          <Legend color={colors.entity} label="entity" />
        </span>
      </div>

      <div className="flex flex-col gap-4 xl:flex-row">
        {/* flex-1 is xl-only: its flex-basis:0 collides with the explicit h-[75vh]
            when this is a flex-col item (below xl), collapsing the canvas to ~0px.
            At xl+ (flex-row) flex-1 only affects the width axis, which is safe. */}
        <div ref={wrap} className="h-[75vh] min-w-0 overflow-hidden rounded-lg border border-border xl:flex-1">
          {error ? (
            <div className="grid h-full place-items-center text-sm text-destructive">{error}</div>
          ) : !graph ? (
            <div className="grid h-full place-items-center"><Loader2 className="size-6 animate-spin text-primary" /></div>
          ) : graph.nodes.length === 0 ? (
            <div className="grid h-full place-items-center px-8 text-center text-sm text-muted-foreground">
              Empty graph — ingest documents and equipment, procedures, and failures wire together here.
            </div>
          ) : (
            <ForceGraph3D
              key={dark ? "dark" : "light"}
              width={size.w}
              height={size.h}
              graphData={graph}
              backgroundColor="rgba(0,0,0,0)"
              showNavInfo={false}
              nodeColor={nodeColor as never}
              nodeVal={((n: { type: string }) => (n.type === "EQUIPMENT_TAG" ? 6 : n.type === "DOCUMENT" ? 3 : 2)) as never}
              nodeRelSize={6}
              nodeOpacity={0.95}
              nodeLabel={((n: { type: string; name: string }) =>
                `<div style="${TOOLTIP_STYLE}"><div style="color:var(--primary);font-size:9px;letter-spacing:.05em;text-transform:uppercase;">${n.type}</div>${n.name}</div>`
              ) as never}
              onNodeClick={((n: { id: string }) => {
                const node = nodesById.get(n.id);
                if (node) setSelected({ kind: "node", node });
              }) as never}
              onBackgroundClick={() => setSelected(null)}
              linkColor={(() => colors.link) as never}
              linkOpacity={0.35}
              linkWidth={0.5}
              linkLabel={((l: { type: string }) =>
                `<div style="${TOOLTIP_STYLE}">${l.type}</div>`
              ) as never}
              onLinkClick={((l: { id: string }) => {
                const edge = edgesById.get(l.id);
                if (edge) setSelected({ kind: "edge", edge });
              }) as never}
              cooldownTicks={140}
            />
          )}
        </div>

        <aside className="flex w-full shrink-0 flex-col gap-3 xl:w-96">
          {selected?.kind === "node" && <NodeInfo node={selected.node} onClose={() => setSelected(null)} />}
          {selected?.kind === "edge" && (
            <EdgeInfo
              edge={selected.edge}
              sourceLabel={nodesById.get(selected.edge.source)?.label ?? selected.edge.source}
              targetLabel={nodesById.get(selected.edge.target)?.label ?? selected.edge.target}
              onClose={() => setSelected(null)}
            />
          )}

          <form
            onSubmit={(e) => { e.preventDefault(); run(); }}
            className="flex gap-2"
          >
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ask about the graph…"
              className="min-w-0 flex-1 rounded-md border border-input bg-card px-3 py-2 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring md:text-sm"
            />
            <Button type="submit" size="icon" aria-label="Ask" disabled={chat.busy || !q.trim()}>
              {chat.busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            </Button>
          </form>
          <div className="min-h-0 flex-1 overflow-auto">
            {chat.error && <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">{chat.error}</div>}
            {chat.res ? (
              <AnswerView res={chat.res} compact />
            ) : (
              !chat.busy && (
                <p className="text-sm text-muted-foreground">
                  Ask about any equipment, failure, or procedure in the graph — answers cite the source documents.
                </p>
              )
            )}
          </div>
        </aside>
      </div>
    </div>
  );

  function run() {
    if (!q.trim()) return;
    chat.run(q);
  }
}

function InfoCard({
  kicker,
  title,
  onClose,
  closeLabel,
  rows,
}: {
  kicker: string;
  title: string;
  onClose: () => void;
  closeLabel: string;
  rows: [string, string][];
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-primary/40 bg-card p-3 animate-fade-up">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="font-mono text-[10px] uppercase tracking-wider text-primary">{kicker}</span>
          <span className="text-sm font-medium">{title}</span>
        </div>
        <button onClick={onClose} aria-label={closeLabel} className="rounded-sm p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
          <X className="size-3.5" />
        </button>
      </div>
      {rows.length > 0 && (
        <dl className="flex flex-col gap-1 border-t border-border pt-2">
          {rows.map(([key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-3 text-xs">
              <dt className="shrink-0 text-muted-foreground">{key}</dt>
              <dd className="truncate font-mono text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function NodeInfo({ node, onClose }: { node: GraphNode; onClose: () => void }) {
  return (
    <InfoCard
      kicker={node.type}
      title={node.label}
      rows={Object.entries(node.metadata ?? {})}
      onClose={onClose}
      closeLabel="Close node details"
    />
  );
}

function EdgeInfo({
  edge,
  sourceLabel,
  targetLabel,
  onClose,
}: {
  edge: GraphEdge;
  sourceLabel: string;
  targetLabel: string;
  onClose: () => void;
}) {
  return (
    <InfoCard
      kicker={edge.type}
      title={`${sourceLabel} → ${targetLabel}`}
      rows={[["weight", edge.weight.toFixed(2)]]}
      onClose={onClose}
      closeLabel="Close edge details"
    />
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <i className="size-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}
