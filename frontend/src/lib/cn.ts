import { clsx, type ClassValue } from "clsx";

/**
 * Conditional class names.
 *
 * A thin wrapper over clsx so that every component imports from one place. If we
 * ever need to swap the merge strategy (e.g. add tailwind-merge to resolve
 * conflicting utilities), there is exactly one file to change.
 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
