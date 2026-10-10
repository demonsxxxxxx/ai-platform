import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { sanitizeSkillName } from "../../utils/skillFilters";
import { normalizeTags, syncSkillMarkdownMetadata } from "./SkillForm.utils";
import { DEFAULT_CONTENT } from "./SkillForm.types";
import type { SkillFormProps, FileEntry } from "./SkillForm.types";
import type { BinaryFileInfo } from "../../types/skill";
import { skillApi } from "../../services/api/skill";
import { SkillFormFullscreen } from "./SkillFormFullscreen";
import { SkillFormNormal } from "./SkillFormNormal";
import { initializeSkillFormFiles, buildSkillFormFileChanges } from "./skillFormFiles";

export function SkillForm({
  skill,
  onSave,
  onCancel,
  isLoading = false,
  onFullscreenChange,
}: SkillFormProps) {
  const { t } = useTranslation();
  const isEditing = !!skill;

  const [name, setName] = useState(skill?.name ?? "");
  const [description, setDescription] = useState(skill?.description ?? "");
  const [tagsInput, setTagsInput] = useState((skill?.tags ?? []).join(", "));
  const [enabled, setEnabled] = useState(skill?.enabled ?? true);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isFullscreen, setIsFullscreen] = useState(false);

  const [files, setFiles] = useState<FileEntry[]>([]);
  const [activeFileIndex, setActiveFileIndex] = useState<number>(0);
  const [binaryFiles, setBinaryFiles] = useState<
    Record<string, BinaryFileInfo>
  >({});
  const [loadingFilePath, setLoadingFilePath] = useState<string | null>(null);

  const generation = useRef(0);
  const filesRef = useRef(files);
  filesRef.current = files;
  const loadingFiles = useRef<Set<FileEntry>>(new Set());
  const originalPaths = useRef<string[]>([]);
  const submitting = useRef(false);

  const toggleFullscreen = useCallback(
    (fs: boolean) => {
      setIsFullscreen(fs);
      onFullscreenChange?.(fs);
      if (fs && window.innerWidth >= 640) {
        toast(t("skills.form.fullscreenHint", "按 Esc 退出全屏"), {
          duration: 2000,
          position: "top-center",
          style: { borderRadius: "10px", background: "#1c1917", color: "#fff" },
        });
      }
    },
    [onFullscreenChange, t],
  );

  useEffect(() => {
    generation.current += 1;
    loadingFiles.current.clear();
    const entries = initializeSkillFormFiles(skill);
    originalPaths.current = entries.map((file) => file.path);
    filesRef.current = entries;
    setFiles(entries);
    setActiveFileIndex(0);
    setBinaryFiles(skill?.binaryFiles ?? {});
    setLoadingFilePath(null);
    setName(skill?.name ?? "");
    setDescription(skill?.description ?? "");
    setTagsInput((skill?.tags ?? []).join(", "));
    setEnabled(skill?.enabled ?? true);
    setErrors({});
    submitting.current = false;
    return () => { generation.current += 1; };
  }, [skill]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isFullscreen) toggleFullscreen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [isFullscreen, toggleFullscreen]);

  // File object identity survives reordering, but not removal, replacement, or edits.
  const loadFileContent = useCallback(
    (index: number) => {
      if (!skill?.name) return;
      const file = files[index];
      if (!file || file.loaded || !filesRef.current.includes(file)) return;
      if (loadingFiles.current.has(file)) return;
      const owner = generation.current;
      const ownsFile = () => owner === generation.current && filesRef.current.includes(file);
      loadingFiles.current.add(file);
      setLoadingFilePath(file.path);
      skillApi.getFile(skill.name, file.originalPath ?? file.path)
        .then((response) => {
          if (!ownsFile()) return;
          const binary = Boolean(response.is_binary);
          if (binary && response.url) {
            setBinaryFiles((current) => ({ ...current, [file.path]: {
              url: response.url!, mime_type: response.mime_type || "application/octet-stream", size: response.size || 0,
            } }));
          }
          setFiles((current) => current.map((candidate) => candidate === file
            ? { ...candidate, content: binary ? "" : response.content, loaded: true, binary } : candidate));
        })
        .catch(() => {
          if (ownsFile()) setErrors((current) => ({ ...current, files: t("skills.loadFailed") }));
        })
        .finally(() => {
          if (owner !== generation.current) return;
          loadingFiles.current.delete(file);
          const pending = [...loadingFiles.current];
          setLoadingFilePath(pending[pending.length - 1]?.path ?? null);
        });
    },
    [skill?.name, files, t],
  );

  useEffect(() => {
    const index = files.findIndex((file) => file.path === "SKILL.md");
    if (index >= 0 && !files[index].loaded) loadFileContent(index);
  }, [files, loadFileContent]);

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    const tags = normalizeTags(tagsInput);

    if (!name.trim()) {
      newErrors.name = t("skills.form.validation.nameRequired");
    } else if (name.trim().length > 100) {
      newErrors.name = t("skills.form.validation.nameTooLong");
    }
    if (!description.trim()) {
      newErrors.description = t("skills.form.validation.descriptionRequired");
    }
    if (tags.some((tag) => tag.length > 30)) {
      newErrors.tags = t("skills.form.validation.tagTooLong");
    }
    const skillMd = files.find((f) => f.path === "SKILL.md");
    if (!skillMd?.loaded || skillMd.binary || !skillMd.content.trim()) {
      newErrors.content = t("skills.form.validation.contentRequired");
    }
    const paths = files.map((f) => f.path.trim());
    if (paths.some((path) => !path)) {
      newErrors.files = t("common.invalidFilePath");
    } else if (new Set(paths).size !== paths.length) {
      newErrors.files = t("skills.form.validation.duplicateFilePaths");
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (isLoading || submitting.current || !validate()) return;

    const tags = normalizeTags(tagsInput);
    const synced = syncSkillMarkdownMetadata(
      files[activeFileIndex]?.path === "SKILL.md"
        ? files[activeFileIndex]?.content || ""
        : files.find((f) => f.path === "SKILL.md")?.content || DEFAULT_CONTENT,
      name.trim(),
      description.trim(),
      tags,
    );

    const changes = buildSkillFormFileChanges(files, originalPaths.current, synced);

    const data = {
      name: sanitizeSkillName(name.trim()),
      description: description.trim(),
      tags,
      content: synced,
      enabled,
      ...changes,
    };

    submitting.current = true;
    const owner = generation.current;
    try {
      const success = await onSave(data);
      if (success && !isEditing && owner === generation.current) {
        setName(""); setDescription(""); setTagsInput(""); setEnabled(true);
        setFiles(initializeSkillFormFiles());
        setActiveFileIndex(0);
      }
    } finally {
      if (owner === generation.current) submitting.current = false;
    }
  };

  const addFile = () => {
    setFiles([...files, { path: "", content: "", loaded: true, dirty: true }]);
    setActiveFileIndex(files.length);
  };

  const removeFile = (index: number) => {
    if (files.length <= 1) return;
    const next = files.filter((_, i) => i !== index);
    filesRef.current = next;
    setFiles(next);
    setActiveFileIndex((active) => Math.max(0, Math.min(active > index ? active - 1 : active, next.length - 1)));
  };

  const updateFilePath = (index: number, path: string) => {
    if (!files[index]?.loaded || files[index]?.binary) return;
    setFiles(files.map((file, i) => i === index ? { ...file, path } : file));
  };

  const updateFileContent = (index: number, content: string) => {
    if (!files[index]?.loaded || files[index]?.binary) return;
    setFiles(files.map((file, i) => i === index ? { ...file, content, dirty: true } : file));
  };

  const removeTag = (targetTag: string) => {
    setTagsInput(
      normalizeTags(tagsInput)
        .filter((tag) => tag !== targetTag)
        .join(", "),
    );
  };

  // When user clicks a file tab, load its content if not yet loaded
  const handleTabSelect = useCallback(
    (index: number) => {
      setActiveFileIndex(index);
      if (!files[index]?.loaded) {
        loadFileContent(index);
      }
    },
    [files, loadFileContent],
  );

  const formActions = {
    name,
    description,
    tagsInput,
    enabled,
    errors,
    isEditing,
    isLoading,
    files,
    activeFileIndex,
    binaryFiles,
    loadingFilePath,
    setName,
    setDescription,
    setEnabled,
    setTagsInput,
    setActiveFileIndex: handleTabSelect,
    updateFilePath,
    updateFileContent,
    removeFile,
    addFile,
    removeTag,
    loadFileContent,
    handleSubmit,
    onCancel,
    toggleFullscreen,
  };

  const formElement = (
    <form
      onSubmit={handleSubmit}
      className={
        isFullscreen
          ? "skill-form skill-form--fullscreen fixed inset-0 z-[1100] flex flex-col bg-[var(--theme-bg)]"
          : "skill-form flex flex-1 flex-col gap-4"
      }
    >
      {isFullscreen ? (
        <SkillFormFullscreen {...formActions} />
      ) : (
        <SkillFormNormal {...formActions} />
      )}
    </form>
  );

  if (isFullscreen) {
    return createPortal(formElement, document.body);
  }
  return formElement;
}
