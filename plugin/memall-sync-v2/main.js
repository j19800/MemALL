"use strict";
const { Plugin, Notice } = require("obsidian");
module.exports = {
  default: class MemAllSyncV2Plugin extends Plugin {
    async onload() {
      console.log("memall-sync-v2: loaded");
      this.addRibbonIcon("sync", "MemALL V2", function() {
        new Notice("MemALL Sync V2 is running");
      });
      var sb = this.addStatusBarItem();
      sb.setText("MemALL V2: OK");
      this.registerInterval(window.setInterval(function() {}, 120000));
    }
  }
};