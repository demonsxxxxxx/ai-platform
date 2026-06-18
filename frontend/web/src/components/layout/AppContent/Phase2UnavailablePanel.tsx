import { ArrowLeft, ShieldAlert } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { getSurfacePolicy } from "./phase1SurfacePolicy";
import type { TabType } from "./types";

const SURFACE_COPY: Partial<Record<TabType, { title: string; body: string }>> = {
  marketplace: {
    title: "Skill marketplace is scheduled for Phase 2",
    body: "Department-scoped marketplace install, enablement, and rollback require backend contracts before this page can be interactive.",
  },
  users: {
    title: "User administration is scheduled for Phase 2",
    body: "Company user lifecycle and department assignment must come from ai-platform admin projections before this page is enabled.",
  },
  roles: {
    title: "Role administration is scheduled for Phase 2",
    body: "RBAC management needs backend role and permission contracts. Current access is enforced by the signed ai-platform principal.",
  },
  feedback: {
    title: "Feedback management is scheduled for Phase 2",
    body: "Feedback submission and reporting require ai-platform feedback projections before this standalone page is enabled.",
  },
  channels: {
    title: "Channel administration is scheduled for Phase 2",
    body: "External channel management needs backend contracts and tenant policy controls before this page is enabled.",
  },
  files: {
    title: "File library is scheduled for Phase 2",
    body: "Run artifacts remain available from chat and playback. A standalone file-library projection is required before this page is enabled.",
  },
  persona: {
    title: "Persona presets are scheduled for Phase 2",
    body: "Persona preset browsing and governance require ai-platform projections before this page is enabled.",
  },
};

export function Phase2UnavailablePanel({ tab }: { tab: TabType }) {
  const navigate = useNavigate();
  const policy = getSurfacePolicy(tab);
  const copy = SURFACE_COPY[tab] ?? {
    title: "This surface is not connected yet",
    body: "This page needs an ai-platform public or admin projection before it can be enabled.",
  };

  return (
    <div className="flex h-full items-center justify-center p-4 sm:p-6">
      <section
        aria-labelledby="phase2-unavailable-title"
        className="w-full max-w-xl rounded-lg border border-amber-200/70 bg-white p-5 shadow-sm dark:border-amber-900/50 dark:bg-stone-900"
      >
        <div className="flex items-start gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
            <ShieldAlert size={20} strokeWidth={1.8} />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase text-amber-700 dark:text-amber-300">
              {policy.classification}
            </p>
            <h2
              id="phase2-unavailable-title"
              className="mt-1 text-base font-semibold text-stone-900 dark:text-stone-50"
            >
              {copy.title}
            </h2>
            <p className="mt-2 text-sm leading-6 text-stone-600 dark:text-stone-300">
              {copy.body}
            </p>
          </div>
        </div>
        <div className="mt-5">
          <button
            type="button"
            onClick={() => navigate("/chat")}
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-stone-900 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-stone-700 focus:outline-none focus:ring-2 focus:ring-stone-400 focus:ring-offset-2 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white"
          >
            <ArrowLeft size={16} strokeWidth={1.8} />
            Back to chat
          </button>
        </div>
      </section>
    </div>
  );
}
