// Test-server-only timing gate. The real converter and its result are unchanged.
export async function observeWordConversion(convert, input) {
  const gate = window.__wordSmokeGate;
  const records = (window.__wordSmokeConversions ??= []);
  const record = { startedAt: Date.now(), gated: Boolean(gate?.armed) };
  records.push(record);
  if (gate?.armed) gate.requested = true;
  try {
    const result = await convert(input);
    record.kind = result.kind; record.htmlLength = result.html.length;
    return result;
  } catch (error) {
    record.error = String(error);
    throw error;
  } finally {
    record.settledAt = Date.now();
    if (gate?.armed) {
      gate.settled = true;
      await new Promise(resolve => { gate.release = resolve; });
      gate.returned = true;
    }
    record.returnedAt = Date.now();
  }
}
