// The world must stay light (docs/GOALS.md, M3 guardrail 2): its code, gzipped, at most 15 KB.
import { readFileSync, readdirSync } from "node:fs";
import { gzipSync } from "node:zlib";

const dir = new URL("../components/world/", import.meta.url);
const files = readdirSync(dir).filter((f) => /\.(ts|tsx)$/.test(f));
const source = files.map((f) => readFileSync(new URL(f, dir), "utf8")).join("\n");
const kb = gzipSync(source).length / 1024;
const LIMIT = 15;
console.log(`world code: ${kb.toFixed(1)} KB gzipped (${files.join(", ")}), limit ${LIMIT} KB`);
if (kb > LIMIT) process.exit(1);
