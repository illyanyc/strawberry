import { useId } from "react";

import { Alert, Field, Switch } from "@/components/ui/form";
import type { HyperparamDraft, SchemaField } from "@/lib/research";

/**
 * Form generated from a model family's hyperparameter JSON Schema (UI-020). Every field shows its
 * range and default; validation messages come from `hyperparamErrors`, which mirrors ml's checks.
 */
export function SchemaForm({
  fields,
  unsupported,
  draft,
  errors,
  onChange,
}: {
  fields: SchemaField[];
  unsupported: string[];
  draft: HyperparamDraft;
  errors: Record<string, string>;
  onChange: (next: HyperparamDraft) => void;
}) {
  if (fields.length === 0 && unsupported.length === 0) return <p className="text-sm text-muted">This family has no hyperparameters.</p>;
  return (
    <div className="space-y-3">
      {unsupported.length > 0 && <Alert tone="warn">The form cannot edit: {unsupported.join(", ")}. The server defaults apply.</Alert>}
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((f) => (
          <SchemaInput key={f.name} field={f} value={draft[f.name]} error={errors[f.name]} onChange={(v) => onChange({ ...draft, [f.name]: v })} />
        ))}
      </div>
    </div>
  );
}

function hint(f: SchemaField): string {
  const range = f.minimum !== undefined || f.maximum !== undefined ? `${f.minimum ?? "−∞"} … ${f.maximum ?? "∞"}` : "";
  const def = f.default !== undefined ? `default ${String(f.default)}` : "";
  return [f.description, range, def].filter(Boolean).join(" · ");
}

function SchemaInput({ field, value, error, onChange }: { field: SchemaField; value: string | boolean | undefined; error?: string; onChange: (v: string | boolean) => void }) {
  const id = useId();
  if (field.type === "boolean") {
    return (
      <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
        <div>
          <p className="text-xs font-medium text-muted">{field.name}</p>
          <p className="text-xs text-muted">{hint(field)}</p>
        </div>
        <Switch checked={value === true} onChange={onChange} label={field.name} />
      </div>
    );
  }
  if (field.enum) {
    return (
      <div className="space-y-1.5">
        <label htmlFor={id} className="block text-xs font-medium text-muted">
          {field.name}
        </label>
        <select id={id} className="h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {field.enum.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted">{hint(field)}</p>
      </div>
    );
  }
  return (
    <Field
      label={field.name}
      inputMode={field.type === "string" ? "text" : "decimal"}
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      hint={hint(field)}
      error={error ?? null}
      placeholder={field.default !== undefined ? String(field.default) : undefined}
    />
  );
}

/** Labelled native select used across research forms. */
export function Select({ label, value, onChange, options, hint: help }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; hint?: string }) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-xs font-medium text-muted">
        {label}
      </label>
      <select id={id} className="h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {help && <p className="text-xs text-muted">{help}</p>}
    </div>
  );
}
