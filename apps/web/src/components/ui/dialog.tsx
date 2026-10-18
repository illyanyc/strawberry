import { type ReactNode, useEffect, useId, useRef } from "react";

import { cn } from "@/lib/utils";

const widths = { sm: "w-[min(28rem,calc(100vw-2rem))]", md: "w-[min(36rem,calc(100vw-2rem))]", lg: "w-[min(56rem,calc(100vw-2rem))]" } as const;

/** Modal dialog on the native <dialog> element (focus trap, Esc to close, inert background). */
export function Dialog({
  open,
  onClose,
  title,
  children,
  size = "sm",
  hideTitle = false,
  placement = "center",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  size?: keyof typeof widths;
  /** Keep the title for screen readers only (command palette). */
  hideTitle?: boolean;
  /** `top` pins the dialog near the top (command palette); `sheet` docks it to the bottom on phones. */
  placement?: "center" | "top" | "sheet";
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Unique per dialog: a step-up dialog can open on top of another dialog.
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        // A click on the backdrop (the dialog element itself, outside the panel) closes it.
        if (e.target === ref.current) onClose();
      }}
      aria-labelledby={titleId}
      className={cn(
        "max-h-[calc(100dvh-2rem)] overflow-hidden rounded-xl border border-border bg-panel p-0 text-fg shadow-2xl",
        widths[size],
        placement === "center" && "m-auto",
        placement === "top" && "mx-auto mt-[12vh]",
        placement === "sheet" && "mx-auto mb-0 mt-auto w-full max-w-none rounded-b-none sm:m-auto sm:w-[min(36rem,calc(100vw-2rem))] sm:rounded-xl",
      )}
    >
      <div className="flex max-h-[calc(100dvh-2rem)] flex-col">
        {hideTitle ? (
          <h2 id={titleId} className="sr-only">
            {title}
          </h2>
        ) : (
          <div className="border-b border-border px-5 py-4">
            <h2 id={titleId} className="text-base font-semibold">
              {title}
            </h2>
          </div>
        )}
        <div className={cn("min-h-0 overflow-y-auto", hideTitle ? "" : "px-5 py-4")}>{children}</div>
      </div>
    </dialog>
  );
}
