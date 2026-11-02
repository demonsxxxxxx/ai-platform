import { createContext } from "react";

/** Narrow-screen consumers close the covering Drawer after opening a preview. */
export const CloseContextPanelContext = createContext<() => void>(() => {});
