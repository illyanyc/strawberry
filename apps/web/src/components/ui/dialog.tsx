import { type ReactNode, useEffect, useId, useRef } from "react";

/** Modal dialog on the native <dialog> element (focus trap, Esc to close, inert background). */
export function Dialog({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
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
      aria-labelledby={titleId}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-xl border border-border bg-panel p-0 text-fg shadow-2xl"
    >
      <div className="border-b border-border px-5 py-4">
        <h2 id={titleId} className="text-base font-semibold">
          {title}
        </h2>
      </div>
      <div className="px-5 py-4">{children}</div>
    </dialog>
  );
}
