// Test-server-only timing gate. The real converter and its result are unchanged.
export async function observeWordConversion(convert, input) {
  const gate = window.__wordSmokeGate;
  if (gate?.armed) gate.requested = true;
  try {
    return await convert(input);
  } finally {
    if (gate?.armed) {
      gate.settled = true;
      await new Promise(resolve => { gate.release = resolve; });
      gate.returned = true;
    }
  }
}
