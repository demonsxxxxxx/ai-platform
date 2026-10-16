import { createElement, type ReactNode } from "react";

async function loadLobeTestProviders() {
  await import("../../../i18n");
  const [{ ThemeProvider }, { LobeUiProvider }] = await Promise.all([
    import("../../../contexts/ThemeContext"),
    import("../../../contexts/LobeUiProvider"),
  ]);
  return (children: ReactNode) => createElement(ThemeProvider, null,
    createElement(LobeUiProvider, null, children));
}

/** Mount the same preference, motion, theme and portal providers as the app. */
export async function createLobeTestRoot(container: Element) {
  const [{ createRoot }, wrap] = await Promise.all([
    import("react-dom/client"), loadLobeTestProviders(),
  ]);
  const attached = !container.isConnected;
  if (attached) document.body.appendChild(container);
  const root = createRoot(container);
  return {
    render(children: ReactNode) {
      root.render(wrap(children));
    },
    unmount() {
      root.unmount();
      if (attached) container.remove();
    },
  };
}

/** Supply the real motion context without adding markup to SSR projections. */
export async function createLobeServerTestRoot() {
  await import("../../../i18n");
  const [{ renderToStaticMarkup }, { default: ConfigProvider }, { motion }] = await Promise.all([
    import("react-dom/server"), import("@lobehub/ui/es/ConfigProvider/index"), import("motion/react"),
  ]);
  return (children: ReactNode) => renderToStaticMarkup(createElement(ConfigProvider, { motion, children }));
}
