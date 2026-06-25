import {
  GraduationCap,
  Code2,
  PenTool,
  Shield,
  Database,
  Zap,
  Package,
  type LucideIcon,
} from "lucide-react";

export function getCategoryIcon(tag: string): LucideIcon {
  const t = tag.toLowerCase();
  if (
    t.includes("学术") ||
    t.includes("academic") ||
    t.includes("论文") ||
    t.includes("paper")
  )
    return GraduationCap;
  if (
    t.includes("编程") ||
    t.includes("coding") ||
    t.includes("code") ||
    t.includes("dev")
  )
    return Code2;
  if (
    t.includes("文案") ||
    t.includes("writing") ||
    t.includes("copy") ||
    t.includes("writer")
  )
    return PenTool;
  if (t.includes("安全") || t.includes("security") || t.includes("安全"))
    return Shield;
  if (
    t.includes("数据") ||
    t.includes("data") ||
    t.includes("数据库") ||
    t.includes("database")
  )
    return Database;
  if (
    t.includes("效率") ||
    t.includes("productivity") ||
    t.includes("工具") ||
    t.includes("tool")
  )
    return Zap;
  return Package;
}
