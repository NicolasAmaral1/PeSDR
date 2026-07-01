// frontend/src/components/InstanceSelector.tsx
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { ChevronDown } from "lucide-react";
import type { InstanceOut } from "../types";
import { Brandmark } from "./Brandmark";

export function InstanceSelector({
  instances,
  value,
  onChange,
}: {
  instances: InstanceOut[];
  value: string | undefined;
  onChange: (id: string) => void;
}) {
  const current = instances.find((i) => i.id === value);
  const label = (i: InstanceOut) => i.display_name || i.channel_label;
  return (
    <Dropdown.Root>
      <Dropdown.Trigger
        data-testid="instance-trigger"
        className="flex w-full items-center justify-between gap-3 bg-slate-900 px-3 py-3 text-left"
      >
        <Brandmark />
        <span className="flex items-center gap-1 text-xs text-slate-300">
          <span data-testid="instance-current">{current ? label(current) : "—"}</span>
          <ChevronDown size={14} />
        </span>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          align="end"
          className="z-50 min-w-48 rounded-md border border-slate-700 bg-slate-900 p-1 text-slate-100 shadow-xl"
        >
          {instances.map((i) => (
            <Dropdown.Item
              key={i.id}
              onSelect={() => onChange(i.id)}
              className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-accent/30"
            >
              {label(i)}
            </Dropdown.Item>
          ))}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
