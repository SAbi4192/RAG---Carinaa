import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * Button.
 *
 * Six variants, three sizes. The loading state REPLACES the label with a spinner
 * rather than sitting beside it, so the button width does not jump when a request
 * starts - a small thing that makes an app feel considered.
 *
 * `disabled` while loading is not automatic: a form submit should lock, but a
 * "cancel" button next to it should stay live.
 */

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "ghost"
  | "danger"
  | "outline"
  | "subtle";

export type ButtonSize = "sm" | "md" | "lg" | "icon";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-brand text-brand-ink shadow-card hover:brightness-110 active:brightness-95 border border-transparent",
  secondary:
    "bg-sunken text-ink border border-line hover:bg-line/60 hover:border-line-strong",
  outline:
    "bg-transparent text-ink border border-line-strong hover:bg-sunken",
  ghost:
    "bg-transparent text-muted border border-transparent hover:bg-sunken hover:text-ink",
  subtle:
    "bg-brand/10 text-brand border border-brand/20 hover:bg-brand/15",
  danger:
    "bg-negative text-white border border-transparent hover:brightness-110 active:brightness-95",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs gap-1.5 rounded-lg",
  md: "h-9.5 px-4 text-sm gap-2 rounded-lg",
  lg: "h-11 px-5 text-sm gap-2 rounded-xl",
  icon: "h-9 w-9 rounded-lg justify-center",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
  /** Icon rendered after the label, e.g. a chevron. */
  trailing?: ReactNode;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    icon,
    trailing,
    fullWidth,
    className,
    children,
    disabled,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex select-none items-center justify-center whitespace-nowrap font-medium",
        "transition-all duration-150 ease-smooth",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        fullWidth && "w-full",
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      ) : (
        icon
      )}
      {children}
      {!loading && trailing}
    </button>
  );
});
