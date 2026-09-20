// ============================================
// Skills Types - Simplified Architecture
// ============================================

// Skill Source Type (based on installed_from)
export type SkillSource = "marketplace" | "manual";

// ============================================
// User Skills Types (from /api/skills/)
// ============================================

// User skill from API list response
export interface UserSkill {
  skill_name: string;
  expected_version: string;
  input_modes: string[];
  requires_file: boolean;
  description: string;
  tags: string[];
  files: string[];
  enabled: boolean;
  file_count: number;
  installed_from: "manual" | "marketplace";
  published_marketplace_name?: string;
  created_at?: string;
  updated_at?: string;
  is_published: boolean;
  marketplace_is_active: boolean;
}

// User skill with files list (from GET /api/skills/{name})
export interface UserSkillDetail {
  files?: string[];
  enabled?: boolean;
  skill_name?: string;
  expected_version?: string;
  input_modes?: string[];
  requires_file?: boolean;
  description?: string;
  tags?: string[];
  is_published?: boolean;
  marketplace_is_active?: boolean;
}

// Skill file content response
export interface SkillFileResponse {
  content: string;
  is_binary?: boolean;
  url?: string;
  mime_type?: string;
  size?: number;
}

// Binary file info stored alongside content in SkillResponse
export interface BinaryFileInfo {
  url: string;
  mime_type: string;
  size: number;
}

// Skill toggle response
export interface SkillToggleResponse {
  skill_name: string;
  enabled: boolean;
  message: string;
}

// ============================================
// Frontend Skill Type (composed from API)
// ============================================

// Full skill used in frontend components
export interface SkillResponse {
  name: string;
  expected_version?: string;
  input_modes?: string[];
  requires_file?: boolean;
  description: string;
  tags: string[];
  enabled: boolean;
  source: SkillSource;
  content?: string; // Main SKILL.md content
  files: Record<string, string>;
  filePaths?: string[]; // file path list without content (for lazy loading)
  binaryFiles?: Record<string, BinaryFileInfo>; // binary file path -> metadata
  file_count: number;
  installed_from: "manual" | "marketplace";
  published_marketplace_name?: string;
  created_at?: string;
  updated_at?: string;
  is_published: boolean;
  marketplace_is_active: boolean;
}

/** Authorized public catalog item returned by GET /api/skills/. */
export interface PublicSkillResponse extends SkillResponse {
  expected_version: string;
  input_modes: string[];
  requires_file: boolean;
}

export interface SelectedSkillRequest {
  skill_id: string;
  expected_version: string;
}

// Skills list response
export interface SkillsResponse {
  skills: UserSkill[];
  total: number;
  skip: number;
  limit: number;
  available_tags: string[];
  effective_permissions: string[];
  effective_permissions_known: boolean;
  catalog_read_resolved: boolean;
}

// Skill Create Request (simplified - write individual files via /files/{path})
export interface SkillCreate {
  name: string;
  description: string;
  tags: string[];
  content: string;
  enabled?: boolean;
  files?: Record<string, string>; // For multi-file support
  source?: SkillSource; // Used by form, not sent to API
}
