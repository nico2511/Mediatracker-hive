import { describe, expect, it } from "vitest";

import {
  GENERATED_PASSWORD_LENGTH,
  generateStrongPassword,
  generatedPasswordHasAmbiguousChar,
  passwordMeetsGeneratorCategories,
} from "./passwordGenerator";

describe("generateStrongPassword", () => {
  it("produit 20 caractères conformes", () => {
    const pwd = generateStrongPassword();
    expect(pwd).toHaveLength(GENERATED_PASSWORD_LENGTH);
    expect(passwordMeetsGeneratorCategories(pwd)).toBe(true);
    expect(generatedPasswordHasAmbiguousChar(pwd)).toBe(false);
  });

  it("couvre les catégories sur plusieurs tirages", () => {
    for (let i = 0; i < 8; i += 1) {
      const pwd = generateStrongPassword();
      expect(passwordMeetsGeneratorCategories(pwd)).toBe(true);
      expect(generatedPasswordHasAmbiguousChar(pwd)).toBe(false);
    }
  });
});