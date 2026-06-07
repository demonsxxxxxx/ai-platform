import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { formatRelativeDate } from "../../../utils/datetime";

export { parseDate } from "../../../utils/datetime";

export function useRelativeTime() {
  const { t } = useTranslation();
  return useCallback(
    (dateStr: string | null): string => formatRelativeDate(t, dateStr),
    [t],
  );
}
