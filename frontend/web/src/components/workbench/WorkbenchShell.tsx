import type { ReactNode } from "react";
import { Boxes, PanelRight, ShieldCheck, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "./workbenchSurface";

export interface WorkbenchShellProps {
  children: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

export function WorkbenchShell({
  children,
  composer,
  rightPanel,
}: WorkbenchShellProps) {
  const { t } = useTranslation();

  const railItems = [
    {
      icon: Sparkles,
      label: t("featureMenu.skillsMarketplace", "Skills Marketplace"),
    },
    {
      icon: Boxes,
      label: t("nav.apps", "Apps"),
    },
    {
      icon: ShieldCheck,
      label: t("workbench.governance", "Governance"),
    },
    {
      icon: PanelRight,
      label: t("workbench.contextPanel", "Context panel"),
    },
  ];

  return (
    <section className={workbenchSurface.root}>
      <div className={workbenchSurface.workspace}>
        <nav
          data-workbench-region="rail"
          className={workbenchSurface.rail}
          aria-label={t("workbench.rail", "Workbench rail")}
        >
          {railItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.label}
                type="button"
                className={workbenchSurface.railButton}
                title={item.label}
                aria-label={item.label}
              >
                <Icon size={18} />
              </button>
            );
          })}
        </nav>

        <div className={workbenchSurface.thread}>
          <div
            data-workbench-region="thread"
            className={workbenchSurface.threadBody}
          >
            {children}
          </div>
          {composer && (
            <div
              data-workbench-region="composer"
              className={workbenchSurface.composer}
            >
              {composer}
            </div>
          )}
        </div>

        <div data-workbench-region="context" className={workbenchSurface.context}>
          {rightPanel}
        </div>
      </div>
    </section>
  );
}
