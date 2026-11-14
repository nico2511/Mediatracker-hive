export type FileStatusKind = "present" | "trash" | "absent" | "downloading";

export function fileStatusLabel(status: string, detailed = false): string {
  switch (status) {
    case "present":
      return "présent";
    case "trash":
      return "Corbeille";
    case "downloading":
      return "En cours";
    case "absent":
      return detailed ? "Absent — fichiers supprimés" : "Absent";
    default:
      return status;
  }
}

export function fileStatusIcon(status: string): string {
  switch (status) {
    case "present":
      return "downloaded";
    case "trash":
      return "trash";
    case "downloading":
      return "progress";
    case "absent":
      return "missing";
    default:
      return "file";
  }
}
