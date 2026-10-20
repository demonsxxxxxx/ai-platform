// Adapted from assistant-ui Elements message-timing.tsx at 727a02dc (MIT).
import { Button, Popover } from "@lobehub/ui/base-ui";
import { Info } from "lucide-react";

export interface TimingStat { label: string; value: string }

export function MessageTiming({ stats, label }: { stats: readonly TimingStat[]; label: string }) {
  if (!stats.length) return null;
  return <Popover placement="topLeft" trigger="click" content={
    <div data-slot="message-timing" className="flex max-w-xs flex-col gap-2 text-xs">
      {stats.map((stat) => <span key={stat.label} className="flex items-baseline justify-between gap-4">
        <span className="text-[var(--theme-text-secondary)]">{stat.label}</span>
        <span className="tabular-nums text-[var(--theme-text)]">{stat.value}</span>
      </span>)}
    </div>
  }>
    <Button type="text" size="small" htmlType="button" aria-label={label} title={label} icon={<Info size={16} />} />
  </Popover>;
}
