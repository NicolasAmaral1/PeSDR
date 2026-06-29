export function Brandmark() {
  return (
    <div className="flex items-center gap-2">
      <span
        className="grid h-7 w-7 place-items-center rounded-md font-display text-sm font-bold text-white"
        style={{ background: "var(--brand-gradient)" }}
        aria-hidden
      >
        a
      </span>
      <span className="font-display text-sm font-semibold tracking-tight text-white">
        Avelum Labs
      </span>
    </div>
  );
}
