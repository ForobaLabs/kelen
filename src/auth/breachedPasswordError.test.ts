import { describe, expect, it } from "vitest";
import i18n from "../i18n";
import { friendlyRegisterError, passwordErrorMessage } from "./api";

const BACKEND = "This password appears in known data breaches. Choose a different one.";

describe("breached password error", () => {
  it("is localised for each language", async () => {
    for (const lng of ["en", "it", "fr"]) {
      await i18n.changeLanguage(lng);
      const msg = passwordErrorMessage(BACKEND, "fallback");
      expect(msg).toBe(i18n.t("login.errPasswordBreached"));
      expect(msg).not.toBe("login.errPasswordBreached");
      expect(msg).not.toBe("fallback");
    }
    await i18n.changeLanguage("en");
  });
  it("passes other details through and falls back when empty", () => {
    expect(passwordErrorMessage("Current password is incorrect", "x")).toBe(
      "Current password is incorrect",
    );
    expect(passwordErrorMessage(undefined, "fallback")).toBe("fallback");
  });
  it("register shows the breach advice, not the generic error", () => {
    expect(friendlyRegisterError(BACKEND)).toBe(i18n.t("login.errPasswordBreached"));
  });
});
