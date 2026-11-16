import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = resolve(import.meta.dirname, ".");

describe("DatabaseBackupRestore", () => {
  it("expose download, import et avertissements import", () => {
    const vue = readFileSync(resolve(ROOT, "DatabaseBackupRestore.vue"), "utf8");
    const css = readFileSync(resolve(ROOT, "backup-restore.css"), "utf8");
    expect(vue).toContain("/api/v1/maintenance/backups/import");
    expect(vue).toContain("/download");
    expect(vue).toContain("Importer une sauvegarde");
    expect(vue).toContain("Télécharger");
    expect(vue).toContain("Identité Hive conservée");
    expect(vue).toContain("SECRET_KEY de l'instance");
    expect(css).toContain(".bk-acts");
    expect(css).toContain(".hive-keep");
    expect(css).toContain("max-width: 720px");
  });
});