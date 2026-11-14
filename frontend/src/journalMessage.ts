/** Human-readable detail line for a journal row (payload is already scrubbed). */

export function journalDetail(type: string, payload: Record<string, unknown>): string {
  for (const key of ["message", "error", "result", "reason"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  const title = payload.title;
  if (typeof title === "string" && title.trim() && type.startsWith("library.")) {
    return "Fichier rangé dans la bibliothèque.";
  }
  if (type === "download.source_switched") {
    const message = payload.message;
    if (typeof message === "string" && message.trim()) {
      return message.trim();
    }
    const from = payload.from_label ?? payload.from_source;
    const to = payload.to_label ?? payload.to_source;
    if (typeof from === "string" && typeof to === "string") {
      return `Bascule de source : ${from} vers ${to}`;
    }
  }
  if (type === "download.source_attempt") {
    const message = payload.message;
    if (typeof message === "string" && message.trim()) {
      return message.trim();
    }
    const label = payload.source_label ?? payload.source;
    const issue = payload.issue;
    if (typeof label === "string" && typeof issue === "string") {
      return `${label} : tentative ${issue}.`;
    }
  }
  if (type.startsWith("download.")) {
    return "Événement de téléchargement.";
  }
  if (type === "hive.fingerprint.started") {
    return "Calcul d'empreinte SHA-256 pour le partage Hive.";
  }
  if (type === "hive.fingerprint.done") {
    return "Empreinte SHA-256 enregistrée pour le partage Hive.";
  }
  if (type === "hive.fingerprint.error") {
    const message = payload.message;
    if (typeof message === "string" && message.trim()) {
      return message.trim();
    }
    return "Échec du calcul d'empreinte pour le partage Hive.";
  }
  if (type.startsWith("hive.transfer.")) {
    for (const key of ["message", "reason"]) {
      const value = payload[key];
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
    }
    return "Événement transfert Hive (historique).";
  }
  return "";
}
