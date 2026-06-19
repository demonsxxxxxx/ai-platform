import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Mail, ExternalLink } from "lucide-react";
import { useAuth } from "../../../hooks/useAuth";

export function ProfileInfoTab() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [imgError, setImgError] = useState(false);
  const adminEmail =
    typeof user?.metadata?.adminContactEmail === "string"
      ? user.metadata.adminContactEmail
      : null;
  const adminUrl =
    typeof user?.metadata?.adminContactUrl === "string"
      ? user.metadata.adminContactUrl
      : null;
  const displayName =
    typeof user?.metadata?.display_name === "string"
      ? user.metadata.display_name
      : null;
  const tenantId =
    typeof user?.metadata?.tenant_id === "string"
      ? user.metadata.tenant_id
      : null;
  const source =
    typeof user?.metadata?.source === "string" ? user.metadata.source : null;

  return (
    <>
      <div className="mb-6 flex flex-col items-center">
        <div className="relative">
          {user?.avatar_url && !imgError ? (
            <img
              src={user.avatar_url}
              alt="Avatar"
              className="size-20 rounded-full border-4 border-white object-cover shadow-lg ring-2 ring-stone-100 dark:border-stone-700 dark:ring-stone-600"
              onError={() => setImgError(true)}
            />
          ) : (
            <div className="flex size-20 items-center justify-center rounded-full border-4 border-white bg-gradient-to-br from-amber-400 to-orange-500 shadow-lg ring-2 ring-stone-100 dark:border-stone-700 dark:ring-stone-600">
              <span className="font-serif text-3xl font-bold text-white">
                {user?.username?.charAt(0).toUpperCase() || "U"}
              </span>
            </div>
          )}
        </div>
        <p className="mt-3 text-center text-xs leading-5 text-stone-400 dark:text-stone-500">
          {t(
            "profile.phase1ReadOnly",
            "Profile data is managed by company login in Phase 1.",
          )}
        </p>
      </div>

      <div className="space-y-0">
        <InfoRow label={t("profile.username")} value={user?.username || "-"} />
        {displayName && (
          <InfoRow
            label={t("profile.displayName", "Display name")}
            value={displayName}
          />
        )}
        <InfoRow label={t("profile.email")} value={user?.email || "-"} />
        {tenantId && (
          <InfoRow
            label={t("profile.tenant", "Tenant")}
            value={tenantId}
          />
        )}
        {source && (
          <InfoRow
            label={t("profile.source", "Source")}
            value={source}
          />
        )}
        {user?.roles && user.roles.length > 0 && (
          <div className="flex items-center justify-between gap-3 py-3.5">
            <span className="shrink-0 text-sm text-stone-500 dark:text-stone-400">
              {t("profile.roles")}
            </span>
            <div className="flex flex-wrap justify-end gap-1.5">
              {user.roles.map((role) => (
                <span
                  key={role}
                  className="inline-flex items-center rounded-full bg-stone-100 px-2 py-0.5 text-xs font-medium text-stone-600 dark:bg-stone-700 dark:text-stone-300"
                >
                  {role}
                </span>
              ))}
            </div>
          </div>
        )}

        {(adminEmail || adminUrl) && (
          <div className="mt-5 space-y-0 border-t border-stone-100 pt-5 dark:border-stone-700/60">
            <p className="mb-1 text-xs text-stone-400 dark:text-stone-500">
              {t("about.contactTitle", "Contact")}
            </p>
            {adminEmail && (
              <a
                href={`mailto:${adminEmail}`}
                className="group flex items-center justify-between gap-3 border-b border-stone-100 py-3.5 dark:border-stone-700/60"
              >
                <span className="flex shrink-0 items-center gap-2 text-sm text-stone-500 dark:text-stone-400">
                  <Mail size={14} />
                  {t("profile.email", "Email")}
                </span>
                <span className="truncate text-sm font-medium text-stone-900 transition-colors group-hover:text-amber-600 dark:text-stone-100 dark:group-hover:text-amber-400">
                  {adminEmail}
                </span>
              </a>
            )}
            {adminUrl && (
              <a
                href={adminUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-center justify-between gap-3 py-3.5"
              >
                <span className="flex shrink-0 items-center gap-2 text-sm text-stone-500 dark:text-stone-400">
                  <ExternalLink size={14} />
                  {t("about.contactSupport", "Support")}
                </span>
                <span className="text-sm font-medium text-stone-400 transition-colors group-hover:text-amber-600 dark:text-stone-500 dark:group-hover:text-amber-400">
                  →
                </span>
              </a>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-stone-100 py-3.5 dark:border-stone-700/60">
      <span className="shrink-0 text-sm text-stone-500 dark:text-stone-400">
        {label}
      </span>
      <span className="truncate text-right text-sm font-medium text-stone-900 dark:text-stone-100">
        {value}
      </span>
    </div>
  );
}
