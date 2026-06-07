interface PanelLoadingStateProps {
  text?: string;
  containerClassName?: string;
  className?: string;
}

export function PanelLoadingState({
  text,
  containerClassName = "h-full",
  className = "",
}: PanelLoadingStateProps) {
  return (
    <div
      className={`flex ${containerClassName} items-center justify-center ${className}`}
    >
      <div className="text-center">
        <div className="relative h-8 w-8 mx-auto mb-4">
          <div className="absolute inset-0 rounded-full border-2 border-stone-200 dark:border-stone-700" />
          <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-stone-500 dark:border-t-stone-400 animate-spin will-change-transform" />
        </div>
        {text ? (
          <p className="text-sm text-stone-500 dark:text-stone-400">{text}</p>
        ) : null}
      </div>
    </div>
  );
}
