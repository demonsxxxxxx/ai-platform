import assert from "node:assert/strict";
import test from "node:test";
import { act, createElement } from "react";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../common/__tests__/lobeUiTestRoot";
import { OptionList } from "../elements/option-list";

function installDom() {
  const fixture = installSelectTestDom();
  document.body.innerHTML = "<div id='root'></div>";
  return { container: document.getElementById("root")!, close: fixture.close };
}

const options = [
  { id: "brief", label: "Brief", description: "A short summary" },
  { id: "full", label: "Full", description: "More explanation" },
] as const;

test("keeps option receipts controlled by the platform choice", async () => {
  const dom = installDom();
  const root = await createLobeTestRoot(dom.container);
  let resolveConfirmation!: () => void;
  let confirmed: string[] = [];
  let choice: readonly string[] | undefined;
  const render = async () => {
    await act(async () => {
      root.render(createElement(OptionList, {
        options,
        onConfirm: (ids: string[]) => {
          confirmed = ids;
          return new Promise<void>((resolve) => { resolveConfirmation = resolve; });
        },
        choice,
      }));
    });
  };

  try {
    await render();
    const first = dom.container.querySelector<HTMLButtonElement>('button[aria-label="Brief"]');
    assert.ok(first);
    await act(async () => { first.click(); });
    assert.deepEqual(confirmed, ["brief"]);
    assert.equal(dom.container.querySelector('[data-state="pending"]') !== null, true);
    assert.equal(dom.container.querySelector('[data-state="receipt"]'), null);

    await act(async () => { resolveConfirmation(); });
    assert.equal(dom.container.querySelector('[data-state="open"]') !== null, true);
    assert.equal(dom.container.querySelector('[data-state="receipt"]'), null);

    choice = ["brief"];
    await render();
    assert.equal(dom.container.querySelector('[data-state="receipt"]') !== null, true);
    assert.equal(dom.container.querySelectorAll('button').length, 0);
    assert.match(dom.container.textContent ?? "", /Brief/);
  } finally {
    await act(async () => root.unmount());
    dom.close();
  }
});

test("preserves controlled multiple selection order and limits disabled options", async () => {
  const dom = installDom();
  const root = await createLobeTestRoot(dom.container);
  let value = ["brief"];
  const changes: string[][] = [];
  try {
    await act(async () => {
      root.render(createElement(OptionList, {
        options: [...options, { id: "disabled", label: "Disabled", disabled: true }],
        selectionMode: "multiple",
        value,
        onValueChange: (next: string[]) => { changes.push(next); value = next; },
        maxSelections: 2,
      }));
    });
    const inputs = dom.container.querySelectorAll<HTMLButtonElement>('[role="checkbox"]');
    assert.equal(inputs.length, 3);
    assert.equal(inputs[0].getAttribute("aria-checked"), "true");
    await act(async () => { inputs[1].click(); });
    assert.deepEqual(changes.at(-1), ["brief", "full"]);
    await act(async () => {
      root.render(createElement(OptionList, {
        options: [...options, { id: "disabled", label: "Disabled", disabled: true }],
        selectionMode: "multiple",
        value,
        onValueChange: (next: string[]) => { changes.push(next); value = next; },
        maxSelections: 2,
      }));
    });
    assert.equal(dom.container.querySelectorAll<HTMLButtonElement>('[role="checkbox"]')[2].disabled, true);
  } finally {
    await act(async () => root.unmount());
    dom.close();
  }
});
