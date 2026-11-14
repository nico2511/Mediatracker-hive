/** Point d'entrée recette : journal unique + exit explicite. */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));
const recette = path.join(dir, "tmdb_id_dialog_recette.mjs");
const result = spawnSync(process.execPath, [recette], { stdio: "inherit" });
process.exit(result.status ?? 1);
