import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "@/App";
import "@/index.css";

import { AuthProvider } from "@/state/auth";
import { ThemeProvider } from "@/state/theme";
import { ToastProvider } from "@/state/toast";
import { WorkspaceProvider } from "@/state/workspace";

/**
 * Provider order matters:
 *
 *   ThemeProvider   outermost - it must be able to theme the error boundary and
 *                   the loading shell, which render before auth resolves.
 *   AuthProvider    next - WorkspaceProvider depends on it.
 *   WorkspaceProvider  innermost of the data providers.
 *   ToastProvider   inside those, so any screen can raise a notification.
 */
const root = document.getElementById("root");

if (!root) {
  throw new Error("Root element #root is missing from index.html.");
}

createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <WorkspaceProvider>
          <ToastProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </ToastProvider>
        </WorkspaceProvider>
      </AuthProvider>
    </ThemeProvider>
  </StrictMode>,
);
