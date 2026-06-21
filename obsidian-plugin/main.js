"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
  return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });

const { Plugin, Notice, TFile, TFolder, parseYaml, stringifyYaml, moment } = require("obsidian");

// ─── Settings ───────────────────────────────────────────────
const DEFAULT_SETTINGS = {
  gatewayUrl: "http://localhost:9919",
  authToken: "",
  syncTargetFolder: "MemALL",
  syncDirection: "both", // push | pull | both
  categoryFolders: true,
  autoSyncOnSave: false,
  lastPushTimestamp: "",
  lastPullTimestamp: "",
};

// ─── Frontmatter helpers ────────────────────────────────────
function parseFrontmatter(content) {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!match) return { frontmatter: {}, body: content };
  try {
    return { frontmatter: parseYaml(match[1]) || {}, body: content.slice(match[0].length) };
  } catch {
    return { frontmatter: {}, body: content };
  }
}

function buildFrontmatter(fm) {
  const yaml = Object.keys(fm).length
    ? stringifyYaml(fm).trimEnd()
    : "";
  return yaml ? `---\n${yaml}\n---\n\n${fm._body || ""}`.trim() : (fm._body || "").trim();
}

function sanitizeFilename(name) {
  return name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "").trim().slice(0, 120) || "untitled";
}

// ─── API client ─────────────────────────────────────────────
class MemAllApi {
  constructor(gatewayUrl, authToken) {
    this.gatewayUrl = gatewayUrl.replace(/\/+$/, "");
    this.authToken = authToken;
  }

  headers() {
    const h = { "Content-Type": "application/json" };
    if (this.authToken) h["Authorization"] = `Bearer ${this.authToken}`;
    return h;
  }

  async request(method, path, body) {
    const url = `${this.gatewayUrl}${path}`;
    const opts = { method, headers: this.headers() };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const txt = await resp.text().catch(() => "");
      throw new Error(`API ${method} ${path}: ${resp.status} ${resp.statusText}${txt ? " — " + txt.slice(0, 200) : ""}`);
    }
    return resp.json();
  }

  async health() {
    return this.request("GET", "/health");
  }

  async capture(content, opts = {}) {
    return this.request("POST", "/capture", { content, ...opts });
  }

  async retrieve(query = "", filters = {}) {
    return this.request("POST", "/retrieve", { query, ...filters });
  }

  async getAllMemories(limit = 2000) {
    return this.retrieve("", { limit });
  }
}

// ─── Plugin ────────────────────────────────────────────────
class MemAllSyncPlugin extends Plugin {
  async onload() {
    await this.loadSettings();
    this.api = new MemAllApi(this.settings.gatewayUrl, this.settings.authToken);

    // Ribbon icon
    this.addRibbonIcon("sync", "MemALL Sync", () => this.showSyncDialog());

    // Status bar
    this.statusBar = this.addStatusBarItem();
    this.statusBar.setText("MemALL: —");

    // Commands
    this.addCommand({
      id: "memall-push",
      name: "Push MemALL → Obsidian",
      callback: () => this.pushSync(),
    });
    this.addCommand({
      id: "memall-pull",
      name: "Pull Obsidian → MemALL",
      callback: () => this.pullSync(),
    });
    this.addCommand({
      id: "memall-full-sync",
      name: "Full bidirectional sync",
      callback: () => this.fullSync(),
    });
    this.addCommand({
      id: "memall-status",
      name: "Show sync status",
      callback: () => this.showStatus(),
    });

    // Settings tab
    this.addSettingTab(new MemAllSyncSettingTab(this.app, this));

    // Auto-sync on save
    if (this.settings.autoSyncOnSave) {
      this.registerEvent(this.app.vault.on("modify", (file) => {
        if (file.path.startsWith(this.settings.syncTargetFolder + "/")) {
          this.pullSingleFile(file);
        }
      }));
    }

    // Initial status check
    this.updateStatusBar();
  }

  onunload() {
    this.statusBar?.remove();
  }

  // ── Settings ──
  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  // ── Status bar ──
  async updateStatusBar() {
    try {
      const h = await this.api.health();
      const count = h.memory_count || "?";
      this.statusBar.setText(`MemALL: ${count} mem · gateway ${h.status === "ok" ? "✓" : "✗"}`);
    } catch {
      this.statusBar.setText("MemALL: offline");
    }
  }

  // ── User dialogs ──
  showSyncDialog() {
    new Notice("MemALL Sync — use Ctrl+P → MemALL to choose action", 4000);
  }

  async showStatus() {
    try {
      const h = await this.api.health();
      const lastPush = this.settings.lastPushTimestamp
        ? moment(this.settings.lastPushTimestamp).fromNow()
        : "never";
      const lastPull = this.settings.lastPullTimestamp
        ? moment(this.settings.lastPullTimestamp).fromNow()
        : "never";
      new Notice(
        `MemALL: ${h.memory_count} memories · Gateway ${h.status}\n` +
        `Push: ${lastPush} · Pull: ${lastPull}\n` +
        `Use Ctrl+P → "MemALL Push/Pull" to sync`,
        6000
      );
    } catch (e) {
      new Notice(`MemALL gateway unreachable: ${e.message}`, 5000);
    }
  }

  // ── Push: MemALL → Obsidian ──
  async pushSync() {
    new Notice("Pushing MemALL → Obsidian...");
    this.statusBar.setText("MemALL: pushing...");

    try {
      const resp = await this.api.retrieve("", { limit: 5000 });
      const memories = resp.results || [];
      const count = await this.writeMemoriesToVault(memories);
      this.settings.lastPushTimestamp = new Date().toISOString();
      await this.saveSettings();
      new Notice(`Push complete: ${count} notes written to ${this.settings.syncTargetFolder}/`);
    } catch (e) {
      new Notice(`Push failed: ${e.message}`, 6000);
      console.error("MemALL push error:", e);
    }
    this.updateStatusBar();
  }

  async writeMemoriesToVault(memories) {
    const vault = this.app.vault;
    const base = this.settings.syncTargetFolder;

    // Ensure base folder exists
    if (!await vault.adapter.exists(base)) {
      await vault.createFolder(base);
    }

    let count = 0;
    const seen = new Set();

    for (const mem of memories) {
      const agent = mem.agent_name || "unknown";
      const category = mem.category || "general";
      const level = mem.level || "P2";
      const subject = mem.subject || "";
      const project = mem.project || "";
      const content = mem.content || "";

      // Skip if content is too short or looks like garbage
      if (content.length < 10) continue;

      // Dedup by content hash prefix
      const dedupKey = content.slice(0, 100);
      if (seen.has(dedupKey)) continue;
      seen.add(dedupKey);

      // Determine path
      let folderPath = base;
      if (this.settings.categoryFolders && category) {
        folderPath = `${base}/${this.sanitizeDir(category)}`;
      }

      // Ensure category folder exists
      if (!await vault.adapter.exists(folderPath)) {
        await vault.createFolder(folderPath);
      }

      // Build filename from subject or content
      const title = subject || content.slice(0, 60).replace(/\s+/g, " ");
      const filename = `${folderPath}/${sanitizeFilename(title)}.md`;

      // Build frontmatter
      const fm = {
        id: mem.id,
        source: "memall",
        agent: agent,
        level: level,
        category: category,
        project: project,
        created_at: mem.occurred_at || mem.created_at || "",
        confidence: mem.confidence,
        visibility: mem.visibility || "private",
        _body: content.trim(),
      };

      // Write Obsidian note
      const fileContent = buildFrontmatter(fm);
      const existing = vault.getAbstractFileByPath(filename);

      if (existing instanceof TFile) {
        await vault.modify(existing, fileContent);
      } else {
        await vault.create(filename, fileContent);
      }
      count++;
    }

    return count;
  }

  sanitizeDir(name) {
    return name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").trim() || "uncategorized";
  }

  // ── Pull: Obsidian → MemALL ──
  async pullSync() {
    new Notice("Pulling Obsidian → MemALL...");
    this.statusBar.setText("MemALL: pulling...");

    try {
      const count = await this.scanVaultForChanges();
      this.settings.lastPullTimestamp = new Date().toISOString();
      await this.saveSettings();
      new Notice(`Pull complete: ${count} notes sent to MemALL`);
    } catch (e) {
      new Notice(`Pull failed: ${e.message}`, 6000);
      console.error("MemALL pull error:", e);
    }
    this.updateStatusBar();
  }

  async scanVaultForChanges() {
    const vault = this.app.vault;
    const base = this.settings.syncTargetFolder;
    const lastPull = this.settings.lastPullTimestamp
      ? new Date(this.settings.lastPullTimestamp).getTime()
      : 0;

    // Collect all markdown files in MemALL folder recursively
    const baseFolder = vault.getAbstractFileByPath(base);
    if (!(baseFolder instanceof TFolder)) {
      new Notice(`Folder "${base}" not found — push first!`, 4000);
      return 0;
    }

    const files = this.collectMarkdownFiles(baseFolder);
    let count = 0;

    for (const file of files) {
      // Skip index/readme files
      const basename = file.name.toLowerCase();
      if (basename === "_index.md" || basename === "_readme.md" || basename === "_metadata.md") continue;

      // Check modification time
      const stat = await vault.adapter.stat(file.path);
      if (stat && lastPull > 0 && stat.mtime <= lastPull) continue;

      // Read file content
      const content = await vault.read(file);
      const { frontmatter, body } = parseFrontmatter(content);

      // Skip if this was originally from MemALL (avoid sync loops)
      if (frontmatter.source === "memall") continue;

      // Send to MemALL
      const agentLine = body.split("\n")[0]?.replace(/^#\s*/, "").trim() || "obsidian-user";
      const opts = {
        agent_name: frontmatter.agent || "obsidian-user",
        subject: frontmatter.subject || frontmatter.title || agentLine.slice(0, 80),
        category: frontmatter.category || frontmatter.tags?.[0] || "note",
        project: frontmatter.project || "",
        level: frontmatter.level || "P2",
        owner: frontmatter.owner || "",
      };

      try {
        await this.api.capture(body.slice(0, 5000), opts);
        count++;
      } catch (err) {
        console.warn(`Failed to pull ${file.path}: ${err.message}`);
      }
    }

    return count;
  }

  collectMarkdownFiles(folder) {
    const files = [];
    for (const child of folder.children) {
      if (child instanceof TFile && child.extension === "md") {
        files.push(child);
      } else if (child instanceof TFolder) {
        files.push(...this.collectMarkdownFiles(child));
      }
    }
    return files;
  }

  pullSingleFile(file) {
    if (file.extension !== "md") return;
    // Debounce: only process if enough time has passed since last edit
    clearTimeout(this._pullTimeout);
    this._pullTimeout = setTimeout(() => {
      this.pullSync();
    }, 3000);
  }

  // ── Full sync ──
  async fullSync() {
    await this.pushSync();
    // Small delay to let Obsidian index the new files
    await new Promise(r => setTimeout(r, 2000));
    await this.pullSync();
    new Notice("Full bidirectional sync complete!");
  }
}

// ─── Settings Tab ────────────────────────────────────────────
class MemAllSyncSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl("h2", { text: "MemALL Sync Settings" });

    new Setting(containerEl)
      .setName("Gateway URL")
      .setDesc("MemALL HTTP gateway address (default: http://localhost:9919)")
      .addText(text => text
        .setPlaceholder("http://localhost:9919")
        .setValue(this.plugin.settings.gatewayUrl)
        .onChange(async val => {
          this.plugin.settings.gatewayUrl = val;
          await this.plugin.saveSettings();
          this.plugin.api = new MemAllApi(val, this.plugin.settings.authToken);
          this.plugin.updateStatusBar();
        }));

    new Setting(containerEl)
      .setName("Auth Token")
      .setDesc("Gateway Bearer token (leave empty for auto-generated)")
      .addText(text => text
        .setPlaceholder("auto-generated")
        .setValue(this.plugin.settings.authToken)
        .onChange(async val => {
          this.plugin.settings.authToken = val;
          await this.plugin.saveSettings();
          this.plugin.api = new MemAllApi(this.plugin.settings.gatewayUrl, val);
        }));

    new Setting(containerEl)
      .setName("Sync Target Folder")
      .setDesc("Obsidian vault folder to store MemALL notes")
      .addText(text => text
        .setPlaceholder("MemALL")
        .setValue(this.plugin.settings.syncTargetFolder)
        .onChange(async val => {
          this.plugin.settings.syncTargetFolder = val || "MemALL";
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("Organize by category")
      .setDesc("Create sub-folders for each memory category")
      .addToggle(toggle => toggle
        .setValue(this.plugin.settings.categoryFolders)
        .onChange(async val => {
          this.plugin.settings.categoryFolders = val;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("Auto-sync on save")
      .setDesc("Auto-pull when notes in the MemALL folder are saved")
      .addToggle(toggle => toggle
        .setValue(this.plugin.settings.autoSyncOnSave)
        .onChange(async val => {
          this.plugin.settings.autoSyncOnSave = val;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("Sync direction")
      .setDesc("push = MemALL→Obsidian, pull = Obsidian→MemALL, both = bidirectional")
      .addDropdown(drop => drop
        .addOption("push", "Push only")
        .addOption("pull", "Pull only")
        .addOption("both", "Bidirectional")
        .setValue(this.plugin.settings.syncDirection)
        .onChange(async val => {
          this.plugin.settings.syncDirection = val;
          await this.plugin.saveSettings();
        }));

    containerEl.createEl("h3", { text: "Sync History" });

    new Setting(containerEl)
      .setName("Last Push")
      .setDesc(this.plugin.settings.lastPushTimestamp
        ? new Date(this.plugin.settings.lastPushTimestamp).toLocaleString()
        : "Never")
      .addButton(btn => btn
        .setButtonText("Push Now")
        .setCta()
        .onClick(() => this.plugin.pushSync()));

    new Setting(containerEl)
      .setName("Last Pull")
      .setDesc(this.plugin.settings.lastPullTimestamp
        ? new Date(this.plugin.settings.lastPullTimestamp).toLocaleString()
        : "Never")
      .addButton(btn => btn
        .setButtonText("Pull Now")
        .setCta()
        .onClick(() => this.plugin.pullSync()));

    new Setting(containerEl)
      .setName("Full Sync")
      .setDesc("Run push then pull in sequence")
      .addButton(btn => btn
        .setButtonText("Full Sync")
        .setCta()
        .onClick(() => this.plugin.fullSync()));

    new Setting(containerEl)
      .setName("Connection Test")
      .setDesc("Check if MemALL gateway is reachable")
      .addButton(btn => btn
        .setButtonText("Test Connection")
        .onClick(async () => {
          try {
            const h = await this.plugin.api.health();
            new Notice(`Gateway OK: ${h.memory_count} memories, uptime ${h.uptime || "?"}s`, 5000);
          } catch (e) {
            new Notice(`Connection failed: ${e.message}`, 6000);
          }
        }));
  }
}

// ─── Exports ────────────────────────────────────────────────
module.exports = {
  default: MemAllSyncPlugin,
};
