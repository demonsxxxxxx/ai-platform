declare module "word-extractor/lib/word-ole-extractor" {
  const WordOleExtractor: unknown;
  export default WordOleExtractor;
}

declare module "word-extractor/lib/buffer-reader" {
  const BufferReader: unknown;
  export default BufferReader;
}

declare module "process/browser" {
  const process: typeof import("node:process");
  export default process;
}
