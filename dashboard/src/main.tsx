import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ClipboardFeedback } from "./ClipboardFeedback";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ClipboardFeedback><App /></ClipboardFeedback>
  </StrictMode>,
);
