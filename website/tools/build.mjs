#!/usr/bin/env node
/* Build the publishable static site into website/dist and refresh the real-data snapshot.
   Usage: node tools/build.mjs [--no-snapshot] [--include-private] [--db <path>] */
import { cpSync, mkdirSync, rmSync, readdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(SITE, "dist");
rmSync(DIST, { recursive: true, force: true });
mkdirSync(DIST, { recursive: true });

for (const f of readdirSync(SITE)) {
  if (["dist", "tools", "node_modules", "data", "README.md"].includes(f)) continue;
  cpSync(path.join(SITE, f), path.join(DIST, f), { recursive: true });
}

if (!process.argv.includes("--no-snapshot")) {
  const args = [path.join(SITE, "tools", "sync.mjs")];
  if (process.argv.includes("--include-private")) args.push("--include-private");
  const i = process.argv.indexOf("--db");
  if (i > 0) args.push("--db", process.argv[i + 1]);
  try { console.log(execFileSync(process.execPath, args, { encoding: "utf8" })); }
  catch (e) { console.warn("快照生成失败（站点仍可用内置样例数据）：", e.message.split("\n")[0]); }
}
console.log(`构建完成 → ${DIST}`);
