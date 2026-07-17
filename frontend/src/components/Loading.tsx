// Boot screen: a small knowledge-graph "forms" (nodes pop in, edges draw) under
// the wordmark — a literal nod to what the product builds. Cyan on near-black.
const NODES = [
  [80, 40], [150, 30], [40, 95], [115, 100], [185, 88], [70, 150], [160, 150],
] as const;
const EDGES = [
  [0, 1], [0, 2], [0, 3], [1, 4], [3, 4], [2, 5], [3, 5], [3, 6], [4, 6],
] as const;

export function Loading() {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-background">
      <div className="flex flex-col items-center gap-8">
        <svg width="220" height="190" viewBox="0 0 220 190" fill="none" aria-hidden>
          {EDGES.map(([a, b], i) => (
            <line
              key={i}
              x1={NODES[a][0]} y1={NODES[a][1]} x2={NODES[b][0]} y2={NODES[b][1]}
              stroke="var(--primary)" strokeWidth="1" strokeDasharray="60"
              style={{ animation: `edge-draw 0.5s ease ${0.5 + i * 0.06}s forwards`, opacity: 0 }}
            />
          ))}
          {NODES.map(([x, y], i) => (
            <circle
              key={i} cx={x} cy={y} r={i === 3 ? 6 : 4}
              fill="var(--primary)"
              style={{ transformOrigin: `${x}px ${y}px`, animation: `node-in 0.5s ease ${i * 0.09}s both` }}
            />
          ))}
        </svg>

        <div className="flex flex-col items-center gap-2 animate-fade-up" style={{ animationDelay: "0.3s" }}>
          <h1 className="text-4xl font-bold tracking-tight">
            MANTR<span className="text-primary">A</span>
          </h1>
          <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-muted-foreground">
            Industrial Knowledge Intelligence
          </p>
        </div>

        <div className="h-px w-40 overflow-hidden bg-border">
          <div className="h-full w-1/3 bg-primary" style={{ animation: "bar-sweep 1.1s ease-in-out infinite" }} />
        </div>
      </div>
    </div>
  );
}
