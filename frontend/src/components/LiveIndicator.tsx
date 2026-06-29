// frontend/src/components/LiveIndicator.tsx
export function LiveIndicator({ connected }: { connected: boolean }) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-1 text-[11px] text-slate-500">
      <span
        className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-amber-500"}`}
        aria-hidden
      />
      {connected ? "ao vivo" : "reconectando…"}
    </div>
  );
}
