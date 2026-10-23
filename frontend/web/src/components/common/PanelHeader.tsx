import { Input } from "@lobehub/ui/base-ui";
/**
 * 统一的面板头部组件
 * 用于所有管理面板的标题和操作区域
 */

import { type ReactNode } from "react";
import { Search } from "lucide-react";

interface PanelHeaderProps {
  /** 面板标题 */
  title: string;
  /** 副标题/描述 */
  subtitle?: string;
  /** 标题图标 */
  icon?: ReactNode;
  /** 右侧操作按钮区域 */
  actions?: ReactNode;
  /** 搜索值 */
  searchValue?: string;
  /** 搜索变化回调 */
  onSearchChange?: (value: string) => void;
  /** 搜索占位符 */
  searchPlaceholder?: string;
  /** 搜索区域右侧附加控件 */
  searchAccessory?: ReactNode;
  /** 额外的头部内容 */
  children?: ReactNode;
  /** 额外 class */
  className?: string;
}

export function PanelHeader({
  title,
  subtitle,
  icon,
  actions,
  searchValue,
  onSearchChange,
  searchPlaceholder,
  searchAccessory,
  children,
  className,
}: PanelHeaderProps) {
  return (
    <div className={className ? `panel-header ${className}` : "panel-header"}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          {icon && (
            <div className="[&>svg]:size-[22px] flex size-10 flex-shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-card)] text-neutral-600 shadow-[0_4px_12px_rgba(18,38,63,0.03)] ring-1 ring-[var(--theme-border)] dark:text-neutral-300">
              {icon}
            </div>
          )}
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold text-neutral-900 dark:text-neutral-50">
              {title}
            </h1>
            {subtitle && (
              <p className="mt-0.5 line-clamp-2 text-sm leading-snug text-neutral-500 dark:text-neutral-400 sm:truncate">
                {subtitle}
              </p>
            )}
          </div>
        </div>
        {actions && (
          <div className="flex flex-nowrap flex-shrink-0 items-center gap-1.5 sm:gap-2">
            {actions}
          </div>
        )}
      </div>

      {/* 搜索框 */}
      {onSearchChange !== undefined && (
        <div className="mt-2 flex items-center gap-2 sm:mt-3">
          <Input
            type="text"
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            className="min-w-0 flex-1"
            prefix={<Search size={18} />}
            aria-label={searchPlaceholder ?? "搜索"}
            placeholder={searchPlaceholder}
          />
          {searchAccessory}
        </div>
      )}

      {/* 额外内容 */}
      {children}
    </div>
  );
}

export default PanelHeader;
