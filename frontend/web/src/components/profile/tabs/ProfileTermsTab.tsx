import { useTranslation, Trans } from "react-i18next";
import {
  Scale,
  ShieldAlert,
  Bot,
  Ban,
  BookOpen,
  AlertTriangle,
  Eye,
} from "lucide-react";

const TERMS_LINK =
  "https://www.gov.cn/zhengce/zhengceku/202307/content_6891752.htm";

const regulationLink = (
  <a
    href={TERMS_LINK}
    target="_blank"
    rel="noopener noreferrer"
    className="relative inline-flex items-center gap-0.5 text-teal-700 dark:text-teal-300 font-medium hover:text-teal-800 dark:hover:text-teal-200 underline decoration-teal-700/30 dark:decoration-teal-300/30 underline-offset-2 transition-colors after:content-['↗'] after:text-[9px] after:ml-0.5 after:opacity-60"
  />
);

const items = [
  {
    icon: BookOpen,
    color: "text-teal-700 dark:text-teal-300",
    dot: "bg-teal-600 dark:bg-teal-400/70",
    bg: "bg-teal-50/60 dark:bg-teal-500/[0.04]",
    key: "termsItem1",
    useTrans: true,
  },
  {
    icon: Bot,
    color: "text-stone-400 dark:text-stone-500",
    dot: "bg-stone-300 dark:bg-stone-600",
    bg: "bg-stone-50/60 dark:bg-stone-700/20",
    key: "termsItem2",
    useTrans: false,
  },
  {
    icon: Ban,
    color: "text-red-500 dark:text-red-400",
    dot: "bg-red-400 dark:bg-red-500/70",
    bg: "bg-red-50/60 dark:bg-red-500/[0.04]",
    key: "termsItem3",
    useTrans: true,
  },
  {
    icon: ShieldAlert,
    color: "text-slate-600 dark:text-stone-300",
    dot: "bg-slate-400 dark:bg-stone-500/70",
    bg: "bg-slate-50/80 dark:bg-stone-800/40",
    key: "termsItem4",
    useTrans: false,
    bold: true,
  },
  {
    icon: AlertTriangle,
    color: "text-slate-600 dark:text-stone-300",
    dot: "bg-slate-400 dark:bg-stone-500/70",
    bg: "bg-slate-50/80 dark:bg-stone-800/40",
    key: "termsItem5",
    useTrans: false,
    bold: true,
  },
  {
    icon: Eye,
    color: "text-sky-500 dark:text-sky-400",
    dot: "bg-sky-400 dark:bg-sky-500/70",
    bg: "bg-sky-50/60 dark:bg-sky-500/[0.04]",
    key: "termsItem6",
    useTrans: false,
    bold: true,
  },
];

export function ProfileTermsTab() {
  const { t } = useTranslation();

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-[var(--theme-bg-sidebar)] dark:bg-stone-950 border border-slate-200/70 dark:border-stone-800">
          <Scale size={17} className="text-teal-700 dark:text-teal-300" />
        </div>
        <div>
          <h3 className="text-sm font-bold text-stone-800 dark:text-stone-100 tracking-tight">
            {t("profile.termsTitle")}
          </h3>
          <p className="text-[10px] text-stone-400 dark:text-stone-500 mt-0.5 tracking-wide uppercase">
            Terms of Service
          </p>
        </div>
      </div>

      {/* Divider */}
      <div className="h-px bg-slate-200/80 dark:bg-stone-800" />

      {/* Items */}
      <div className="space-y-3">
        {items.map((item, i) => {
          const Icon = item.icon;
          const content = item.useTrans ? (
            <Trans
              i18nKey={`profile.${item.key}`}
              components={{ a: regulationLink, strong: <strong /> }}
            />
          ) : item.bold ? (
            <strong>{t(`profile.${item.key}`)}</strong>
          ) : (
            t(`profile.${item.key}`)
          );

          return (
            <div
              key={i}
              className={`group relative flex items-start gap-3 pl-4 pr-3 py-3 rounded-lg border border-transparent ${item.bg} transition-colors duration-200 hover:border-slate-200/70 dark:hover:border-stone-800`}
            >
              {/* Left accent dot */}
              <span
                className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-full ${item.dot} opacity-70 group-hover:opacity-100 transition-opacity`}
              />

              {/* Icon */}
              <Icon
                size={14}
                className={`shrink-0 mt-[3px] ${item.color} opacity-80 group-hover:opacity-100 transition-opacity`}
              />

              {/* Text */}
              <span
                className="text-xs leading-[1.7] text-stone-600 dark:text-stone-300"
                style={{ textAlign: "justify" }}
              >
                {content}
              </span>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-center gap-1.5 pt-1">
        <span className="h-px flex-1 max-w-[40px] bg-slate-200 dark:bg-stone-800" />
        <p className="text-[10px] text-stone-400 dark:text-stone-500 whitespace-nowrap">
          {t("auth.termsHint")}
        </p>
        <span className="h-px flex-1 max-w-[40px] bg-slate-200 dark:bg-stone-800" />
      </div>
    </div>
  );
}
