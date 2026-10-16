import { createRoot } from "react-dom/client";
import "../../i18n";
import "../../styles/tailwind.css";
import "../../styles/base.css";
import { ThemeProvider } from "../../contexts/ThemeContext";
import { LobeUiProvider } from "../../contexts/LobeUiProvider";
import LobeStylePreview from "./LobeStylePreview";

if (import.meta.env.DEV) {
  createRoot(document.getElementById("root")!).render(
    <ThemeProvider><LobeUiProvider><LobeStylePreview /></LobeUiProvider></ThemeProvider>,
  );
}
