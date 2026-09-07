import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ComponentPreview from "./ComponentPreview";
import "@/styles/ui.css";

const root = document.getElementById("root");
if (!root) throw new Error("UI preview root is missing");
createRoot(root).render(
  <StrictMode>
    <ComponentPreview />
  </StrictMode>,
);
