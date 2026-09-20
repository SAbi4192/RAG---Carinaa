/// <reference types="vite/client" />

/**
 * Ambient declarations.
 *
 * Vite handles CSS, SVG and asset imports at build time, but TypeScript does not
 * know about them unless they are declared. Without this file, `import "@/index.css"`
 * is a type error even though it works perfectly at runtime.
 */

declare module "*.css" {
  const content: string;
  export default content;
}

declare module "*.svg" {
  const src: string;
  export default src;
}

declare module "*.png" {
  const src: string;
  export default src;
}

declare module "*.jpg" {
  const src: string;
  export default src;
}

declare module "*.webp" {
  const src: string;
  export default src;
}
