export function SkeletonLine({ width = "100%", height = 12 }: { width?: string | number; height?: number }) {
  return <div className="skeleton" style={{ width, height, marginBottom: 6 }} />;
}

export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="grid-cards">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{ background: "var(--panel-raised)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 14 }}>
          <SkeletonLine width="60%" height={13} />
          <SkeletonLine width="40%" height={10} />
          <div style={{ marginTop: 10, display: "flex", gap: 16 }}>
            <SkeletonLine width={50} height={10} />
            <SkeletonLine width={50} height={10} />
            <SkeletonLine width={50} height={10} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 4, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} style={{ display: "flex", gap: 20 }}>
          {Array.from({ length: cols }).map((_, c) => (
            <SkeletonLine key={c} width={c === 0 ? 90 : 70} height={11} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonFeed({ rows = 5 }: { rows?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonLine key={i} width={`${85 - i * 6}%`} height={11} />
      ))}
    </div>
  );
}
