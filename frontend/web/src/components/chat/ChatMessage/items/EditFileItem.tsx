import { memo } from "react";
import { Pencil } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CollapsiblePill, CopyButton } from "../../../common";
import { DeferredCodeMirrorViewer } from "../../../common/DeferredCodeMirrorViewer";
import { extractText } from "./toolUtils";
import { openPersistentToolPanel } from "./persistentToolPanelState";

const EditFileItem = memo(function EditFileItem({
  args,
  result,
  success,
  isPending,
  cancelled,
}: {
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  success?: boolean;
  isPending?: boolean;
  cancelled?: boolean;
}) {
  const { t } = useTranslation();
  const filePath = (args.file_path as string) || "";
  const fileName = filePath.split("/").pop() || filePath;
  const oldString = (args.old_string as string) || "";
  const newString = (args.new_string as string) || "";

  const canExpand = !!oldString || !!newString || !!result;
  const status = isPending
    ? "loading"
    : cancelled
      ? "cancelled"
      : success
        ? "success"
        : "error";

  const detailContent = canExpand && (
    <div className="p-4 sm:p-5 space-y-3">
      <div className="group/args relative flex items-center gap-2 px-3 py-2 rounded-lg bg-stone-100 dark:bg-stone-800 text-sm text-stone-500 dark:text-stone-400 font-mono">
        <span className="truncate">{filePath}</span>
        <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/args:opacity-100 transition-opacity">
          <CopyButton text={filePath} size={12} />
        </div>
      </div>
      {oldString && (
        <div>
          <div className="text-xs text-red-500 dark:text-red-400 mb-1.5 font-semibold uppercase tracking-wider">
            {t("chat.message.toolEditRemoved")}
          </div>
          <div className="relative group rounded-lg border border-red-200/60 dark:border-red-800/40 bg-red-50 dark:bg-red-950/30 overflow-hidden">
            <DeferredCodeMirrorViewer
              value={oldString}
              filePath={filePath}
              lineNumbers={false}
              fontSize="0.8rem"
              className="[&_.cm-editor]:bg-transparent dark:[&_.cm-editor]:bg-transparent"
            />
            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <CopyButton
                text={oldString}
                size={14}
                className="!bg-white/80 dark:!bg-stone-800/80 backdrop-blur-sm !rounded-md !border !border-red-200 dark:!border-red-800"
              />
            </div>
          </div>
        </div>
      )}
      {newString && (
        <div>
          <div className="text-xs text-emerald-500 dark:text-emerald-400 mb-1.5 font-semibold uppercase tracking-wider">
            {t("chat.message.toolEditAdded")}
          </div>
          <div className="relative group rounded-lg border border-emerald-200/60 dark:border-emerald-800/40 bg-emerald-50 dark:bg-emerald-950/30 overflow-hidden">
            <DeferredCodeMirrorViewer
              value={newString}
              filePath={filePath}
              lineNumbers={false}
              fontSize="0.8rem"
              className="[&_.cm-editor]:bg-transparent dark:[&_.cm-editor]:bg-transparent"
            />
            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <CopyButton
                text={newString}
                size={14}
                className="!bg-white/80 dark:!bg-stone-800/80 backdrop-blur-sm !rounded-md !border !border-emerald-200 dark:!border-emerald-800"
              />
            </div>
          </div>
        </div>
      )}
      {result &&
        (() => {
          const text = extractText(result);
          return text ? (
            <pre className="group/result relative text-xs text-stone-500 dark:text-stone-400 whitespace-pre-wrap break-words p-3 rounded-lg bg-stone-50 dark:bg-stone-900 border border-stone-200/60 dark:border-stone-700/50">
              {text}
              <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
                <CopyButton
                  text={text}
                  size={12}
                  className="!bg-white/80 dark:!bg-stone-800/80 backdrop-blur-sm !rounded-md"
                />
              </div>
            </pre>
          ) : null;
        })()}
    </div>
  );

  return (
    <>
      <CollapsiblePill
        status={status}
        icon={<Pencil size={12} className="shrink-0 opacity-50" />}
        label={`${t("chat.message.toolEdit")} ${filePath || ""}`}
        variant="tool"
        formatLabel={false}
        expandable={canExpand}
        onPanelOpen={() => {
          if (!canExpand) return;
          openPersistentToolPanel({
            title: `${t("chat.message.toolEdit")} ${fileName || filePath}`,
            icon: <Pencil size={16} />,
            status,
            subtitle: filePath,
            children: detailContent,
          });
        }}
      >
        {canExpand && (
          <div className="mt-2 ml-4 pl-3 border-l-2 border-stone-200/60 dark:border-stone-700/50 max-h-80 overflow-y-auto min-w-0">
            <div className="group/args relative flex items-center gap-2 mb-2 px-2 py-1.5 rounded-md bg-stone-100 dark:bg-stone-800 text-xs text-stone-500 dark:text-stone-400 font-mono">
              <span className="truncate">{filePath}</span>
              <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/args:opacity-100 transition-opacity">
                <CopyButton text={filePath} size={12} />
              </div>
            </div>
            {oldString && (
              <div className="mb-2">
                <div className="text-xs text-red-500 dark:text-red-400 mb-1 font-medium">
                  -
                </div>
                <div className="relative group overflow-y-auto rounded-md border border-red-200/60 dark:border-red-800/40 bg-red-50 dark:bg-red-950/30">
                  <DeferredCodeMirrorViewer
                    value={oldString}
                    filePath={filePath}
                    lineNumbers={false}
                    fontSize="0.75rem"
                    className="[&_.cm-editor]:bg-transparent dark:[&_.cm-editor]:bg-transparent"
                  />
                  <div className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <CopyButton
                      text={oldString}
                      size={12}
                      className="!bg-white/80 dark:!bg-stone-800/80 backdrop-blur-sm !rounded-md !border !border-red-200 dark:!border-red-800"
                    />
                  </div>
                </div>
              </div>
            )}
            {newString && (
              <div className="mb-2">
                <div className="text-xs text-emerald-500 dark:text-emerald-400 mb-1 font-medium">
                  +
                </div>
                <div className="relative group overflow-y-auto rounded-md border border-emerald-200/60 dark:border-emerald-800/40 bg-emerald-50 dark:bg-emerald-950/30">
                  <DeferredCodeMirrorViewer
                    value={newString}
                    filePath={filePath}
                    lineNumbers={false}
                    fontSize="0.75rem"
                    className="[&_.cm-editor]:bg-transparent dark:[&_.cm-editor]:bg-transparent"
                  />
                  <div className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <CopyButton
                      text={newString}
                      size={12}
                      className="!bg-white/80 dark:!bg-stone-800/80 backdrop-blur-sm !rounded-md !border !border-emerald-200 dark:!border-emerald-800"
                    />
                  </div>
                </div>
              </div>
            )}
            {result &&
              (() => {
                const text = extractText(result);
                return text ? (
                  <pre className="group/result relative text-xs text-stone-500 dark:text-stone-400 whitespace-pre-wrap break-words mt-1 overflow-y-auto min-w-0">
                    {text}
                    <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
                      <CopyButton text={text} size={12} />
                    </div>
                  </pre>
                ) : null;
              })()}
          </div>
        )}
      </CollapsiblePill>
    </>
  );
});

export { EditFileItem };
