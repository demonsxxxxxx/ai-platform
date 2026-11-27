/** Keep the file type visible when only the base name needs to be shortened. */
export function AttachmentFilename({ name, className }: { name: string; className?: string }) {
  const dot = name.lastIndexOf(".");
  const hasExtension = dot > 0 && /^\.[\p{L}\p{N}]{1,10}$/u.test(name.slice(dot));
  return (
    <span data-attachment-filename className={`flex min-w-0 ${className ?? ""}`}>
      <span className="min-w-0 truncate">{hasExtension ? name.slice(0, dot) : name}</span>
      {hasExtension && <span className="shrink-0">{name.slice(dot)}</span>}
    </span>
  );
}
