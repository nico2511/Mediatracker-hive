export function tmdbRatingLabel(value: number | null | undefined): string {
  if (value == null) {
    return "";
  }
  return value.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

export function tmdbRatingCardAriaLabel(value: number | null | undefined): string {
  const label = tmdbRatingLabel(value);
  if (!label) {
    return "";
  }
  return `Note TMDB ${label} sur 10`;
}

export function tmdbVoteCountLabel(count: number | null | undefined): string {
  if (count == null) {
    return "";
  }
  return count.toLocaleString("fr-FR");
}

export function tmdbVoteCountPhrase(count: number | null | undefined): string {
  const label = tmdbVoteCountLabel(count);
  if (!label) {
    return "";
  }
  if (count === 1) {
    return "1 vote";
  }
  return `${label} votes`;
}