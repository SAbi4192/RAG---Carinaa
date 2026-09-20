import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cn } from "@/lib/cn";

/**
 * Form controls.
 *
 * Every field renders its own label, hint and error, wired together with
 * `htmlFor` / `aria-describedby` / `aria-invalid`. Doing this centrally is the
 * only reliable way to get accessibility right: when each call site hand-rolls
 * its own label markup, the ids inevitably drift and the error text stops being
 * announced.
 */

const CONTROL_BASE =
  "w-full rounded-lg border bg-surface px-3 text-sm text-ink transition-colors duration-150 " +
  "placeholder:text-faint disabled:cursor-not-allowed disabled:opacity-60 " +
  "focus:outline-none focus:ring-2 focus:ring-brand/35 focus:border-brand";

function controlClasses(invalid?: boolean, className?: string) {
  return cn(CONTROL_BASE, invalid ? "border-negative" : "border-line-strong", className);
}

interface FieldShellProps {
  label?: ReactNode;
  hint?: ReactNode;
  error?: string;
  required?: boolean;
  htmlFor?: string;
  descriptionId?: string;
  errorId?: string;
  children: ReactNode;
  className?: string;
  /** Rendered to the right of the label, e.g. a character counter. */
  trailingLabel?: ReactNode;
}

function FieldShell({
  label,
  hint,
  error,
  required,
  htmlFor,
  descriptionId,
  errorId,
  children,
  className,
  trailingLabel,
}: FieldShellProps) {
  return (
    <div className={cn("space-y-1.5", className)}>
      {label || trailingLabel ? (
        <div className="flex items-baseline justify-between gap-2">
          {label ? (
            <label htmlFor={htmlFor} className="text-xs font-medium text-ink">
              {label}
              {required ? <span className="ml-0.5 text-negative">*</span> : null}
            </label>
          ) : (
            <span />
          )}
          {trailingLabel ? <span className="text-2xs text-faint">{trailingLabel}</span> : null}
        </div>
      ) : null}

      {children}

      {/* Hint and error share a slot; showing both at once is noise, and the
          error is the more actionable of the two. */}
      {error ? (
        <p id={errorId} className="text-2xs text-negative">
          {error}
        </p>
      ) : hint ? (
        <p id={descriptionId} className="text-2xs leading-relaxed text-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: string;
  icon?: ReactNode;
  trailingLabel?: ReactNode;
  wrapperClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, icon, trailingLabel, wrapperClassName, className, id, required, ...rest },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const descriptionId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;

  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      required={required}
      htmlFor={inputId}
      descriptionId={descriptionId}
      errorId={errorId}
      className={wrapperClassName}
      trailingLabel={trailingLabel}
    >
      <div className="relative">
        {icon ? (
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint">
            {icon}
          </span>
        ) : null}
        <input
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : hint ? descriptionId : undefined}
          className={controlClasses(Boolean(error), cn("h-9.5", icon && "pl-9", className))}
          {...rest}
        />
      </div>
    </FieldShell>
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: string;
  wrapperClassName?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, hint, error, wrapperClassName, className, id, required, rows = 4, ...rest },
  ref,
) {
  const generatedId = useId();
  const areaId = id ?? generatedId;
  const descriptionId = `${areaId}-hint`;
  const errorId = `${areaId}-error`;

  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      required={required}
      htmlFor={areaId}
      descriptionId={descriptionId}
      errorId={errorId}
      className={wrapperClassName}
    >
      <textarea
        ref={ref}
        id={areaId}
        rows={rows}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : hint ? descriptionId : undefined}
        className={controlClasses(Boolean(error), cn("resize-y py-2.5 leading-relaxed", className))}
        {...rest}
      />
    </FieldShell>
  );
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: string;
  wrapperClassName?: string;
  options?: { value: string; label: string; disabled?: boolean }[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, error, wrapperClassName, className, id, required, options, children, ...rest },
  ref,
) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const descriptionId = `${selectId}-hint`;
  const errorId = `${selectId}-error`;

  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      required={required}
      htmlFor={selectId}
      descriptionId={descriptionId}
      errorId={errorId}
      className={wrapperClassName}
    >
      <select
        ref={ref}
        id={selectId}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : hint ? descriptionId : undefined}
        className={controlClasses(Boolean(error), cn("h-9.5 cursor-pointer pr-8", className))}
        {...rest}
      >
        {options?.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
        {children}
      </select>
    </FieldShell>
  );
});

/**
 * A labelled on/off switch.
 *
 * Rendered as a real checkbox with `role="switch"`, not a styled div, so it is
 * keyboard-operable and announced correctly for free.
 */
export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
  id,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
  id?: string;
}) {
  const generatedId = useId();
  const toggleId = id ?? generatedId;

  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={toggleId} className="text-xs font-medium text-ink">
          {label}
        </label>
        {hint ? <p className="mt-0.5 text-2xs leading-relaxed text-faint">{hint}</p> : null}
      </div>

      <button
        id={toggleId}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors duration-200",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40",
          "disabled:cursor-not-allowed disabled:opacity-50",
          checked ? "border-brand bg-brand" : "border-line-strong bg-sunken",
        )}
      >
        <span
          className={cn(
            "inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform duration-200 ease-smooth",
            checked ? "translate-x-5" : "translate-x-0.5",
          )}
        />
      </button>
    </div>
  );
}
