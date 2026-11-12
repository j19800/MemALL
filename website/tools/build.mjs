#!/usr/bin/env node
/* Build the publishable static site into website/dist and refresh the real-data snapshot.
   Usage: node tools/build.mjs [--no-snapshot] [--include-private] [--db <path>]

   Atomic publish: build into dist.staging, then swap dist in place. A live static
   server holding dist as cwd keeps serving the OLD inode during the build window
   and lands on the NEW content only after the rename completes — so an update never
   produces a mid-build 404 (the old rm-then-cp approach deleted dist out from under
   the running server, breaking every route until the server was restarted).
   Snapshot sync is done into staging too, so a failing snapshot can't corrupt dist. */
import { cpSync, mkdirSync, rmSync, readdirSync, renameSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(SITE, "dist");
const STAGING = path.join(SITE, "dist.staging");
const OLD = path.join(SITE, "dist.old");

rmSync(STAGING, { recursive: true, force: true });
mkdirSync(STAGING, { recursive: true });

for (const f of readdirSync(SITE)) {
  if (["dist", "dist.staging", "dist.old", "tools", "node_modules", "data", "README.md"].includes(f)) continue;
  cpSync(path.join(SITE, f), path.join(STAGING, f), { recursive: true });
}

if (!process.argv.includes("--no-snapshot")) {
  const args = [path.join(SITE, "tools", "sync.mjs"),
                "--out", path.join(STAGING, "data", "snapshot.json")];
  if (process.argv.includes("--include-private")) args.push("--include-private");
  const i = process.argv.indexOf("--db");
  if (i > 0) args.push("--db", process.argv[i + 1]);
  try { console.log(execFileSync(process.execPath, args, { encoding: "utf8" })); }
  catch (e) { console.warn("快照生成失败（站点仍可用内置样例数据）：", e.message.split("\n")[0]); }
}

// Atomic swap: dist -> dist.old, staging -> dist, drop old.
rmSync(OLD, { recursive: true, force: true });
if (existsSync(DIST)) renameSync(DIST, OLD);
renameSync(STAGING, DIST);
rmSync(OLD, { recursive: true, force: true });

console.log(`构建完成 → ${DIST}（原子发布，运行中服务器无缝切换）`);
