/**
 * Web Worker for computing SHA-256 hash of a file.
 */
self.onmessage = async (e: MessageEvent<{ file: File }>) => {
  const { file } = e.data;

  try {
    const hashBuffer = await crypto.subtle.digest(
      "SHA-256",
      await file.arrayBuffer(),
    );
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const hashHex = hashArray
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");

    self.postMessage({ hash: hashHex });
  } catch (error) {
    self.postMessage({
      error: error instanceof Error ? error.message : "Hash computation failed",
    });
  }
};
