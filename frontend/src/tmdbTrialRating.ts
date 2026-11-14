export function tmdbTrialRatingLabel(value: number | null | undefined): string {
  if (value == null) {
    return "";
  }
  return value.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}
