/** Génération locale de mots de passe forts (navigateur uniquement). */

export const GENERATED_PASSWORD_LENGTH = 20;

const LOWER = "abcdefghjkmnpqrstuvwxyz";
const UPPER = "ABCDEFGHJKMNPQRSTUVWXYZ";
const DIGITS = "23456789";
const SYMBOLS = "!@#$%^&*-_=+";

const AMBIGUOUS = new Set("0Oo1lI");

const ALL = LOWER + UPPER + DIGITS + SYMBOLS;

function randomInt(max: number): number {
  const range = 0x1_0000_0000;
  const limit = range - (range % max);
  const buf = new Uint32Array(1);
  while (true) {
    crypto.getRandomValues(buf);
    const n = buf[0];
    if (n < limit) {
      return n % max;
    }
  }
}

function pick(chars: string): string {
  return chars[randomInt(chars.length)]!;
}

function shuffle(values: string[]): string[] {
  const out = [...values];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = randomInt(i + 1);
    const tmp = out[i]!;
    out[i] = out[j]!;
    out[j] = tmp;
  }
  return out;
}

export function generatedPasswordHasAmbiguousChar(value: string): boolean {
  for (const ch of value) {
    if (AMBIGUOUS.has(ch)) {
      return true;
    }
  }
  return false;
}

export function passwordMeetsGeneratorCategories(value: string): boolean {
  const hasLower = [...value].some((ch) => ch >= "a" && ch <= "z");
  const hasUpper = [...value].some((ch) => ch >= "A" && ch <= "Z");
  const hasDigit = [...value].some((ch) => ch >= "0" && ch <= "9");
  const hasSymbol = [...value].some((ch) => !/[A-Za-z0-9]/.test(ch));
  return hasLower && hasUpper && hasDigit && hasSymbol;
}

export function generateStrongPassword(length = GENERATED_PASSWORD_LENGTH): string {
  const size = Math.max(12, length);
  const chars: string[] = [pick(LOWER), pick(UPPER), pick(DIGITS), pick(SYMBOLS)];
  while (chars.length < size) {
    chars.push(pick(ALL));
  }
  return shuffle(chars).join("");
}