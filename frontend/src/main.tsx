import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "virtual:mantine-vars.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./theme/tokens.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
