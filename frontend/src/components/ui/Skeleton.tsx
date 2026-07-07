export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-line/50 ${className}`} />;
}

export function SkeletonTableRows({
  rows = 6,
  colSpan,
}: {
  rows?: number;
  colSpan: number;
}) {
  return (
    <>
      {Array.from({ length: rows }, (_, i) => (
        <tr key={i} className="border-b border-line/50">
          <td colSpan={colSpan} className="px-4 py-2.5">
            <Skeleton className="h-3 w-full max-w-[85%]" />
          </td>
        </tr>
      ))}
    </>
  );
}

export function SkeletonRow({ columns }: { columns: string[] }) {
  return (
    <tr className="border-b border-line/50">
      {columns.map((widthClass, i) => (
        <td key={i} className="px-2 py-1.5">
          <Skeleton className={`h-3 ${widthClass}`} />
        </td>
      ))}
    </tr>
  );
}

export function SkeletonCard() {
  return (
    <div className="rounded-lg border border-line bg-surface-elevated px-3 py-2">
      <Skeleton className="h-2.5 w-2/3" />
      <Skeleton className="mt-2 h-4 w-1/2" />
    </div>
  );
}

export function SkeletonPill({ className = "" }: { className?: string }) {
  return (
    <div className={`rounded-md border border-line px-3 py-1.5 ${className}`}>
      <Skeleton className="h-3 w-full" />
    </div>
  );
}
