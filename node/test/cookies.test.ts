import { describe, expect, it } from "vitest";
import {
  parseCookieLine,
  parseCookieLines,
} from "../src/browser-mode/cookies.js";

describe("parseCookieLine", () => {
  it("parses a session-only line exactly like the backend serializer produces", () => {
    const cookie = parseCookieLine(
      "c_user=100000000000001; Domain=.facebook.com; Path=/; HttpOnly",
    );
    expect(cookie).toEqual({
      name: "c_user",
      value: "100000000000001",
      domain: ".facebook.com",
      path: "/",
      httpOnly: true,
    });
  });

  it("parses secure + httpOnly + expires", () => {
    const cookie = parseCookieLine(
      "xs=abc123def456; Domain=.facebook.com; Path=/; Expires=1758432025; Secure; HttpOnly",
    );
    expect(cookie).toEqual({
      name: "xs",
      value: "abc123def456",
      domain: ".facebook.com",
      path: "/",
      expires: 1758432025,
      secure: true,
      httpOnly: true,
    });
  });

  it("ignores unknown attributes (SameSite) without failing", () => {
    const cookie = parseCookieLine(
      "wd=abc; Domain=.facebook.com; Path=/; SameSite=Lax",
    );
    expect(cookie).toEqual({
      name: "wd",
      value: "abc",
      domain: ".facebook.com",
      path: "/",
    });
  });

  it("falls back to / for a missing Path", () => {
    const cookie = parseCookieLine("m=1; Domain=.instagram.com");
    expect(cookie).toEqual({
      name: "m",
      value: "1",
      domain: ".instagram.com",
      path: "/",
    });
  });

  it("returns null for a line without a name=value pair", () => {
    expect(parseCookieLine("; Domain=.facebook.com")).toBeNull();
    expect(parseCookieLine("")).toBeNull();
  });

  it("returns null for a line without a Domain", () => {
    expect(parseCookieLine("x=1; Path=/; HttpOnly")).toBeNull();
  });
});

describe("parseCookieLines", () => {
  it("parses every line and drops malformed ones", () => {
    const cookies = parseCookieLines([
      "c_user=1; Domain=.facebook.com; Path=/; HttpOnly",
      "garbage",
      "xs=2; Domain=.facebook.com; Path=/; Secure; HttpOnly",
    ]);
    expect(cookies).toHaveLength(2);
    expect(cookies.map((c) => c.name)).toEqual(["c_user", "xs"]);
  });

  it("returns [] for undefined / null / empty input", () => {
    expect(parseCookieLines(undefined)).toEqual([]);
    expect(parseCookieLines(null)).toEqual([]);
    expect(parseCookieLines([])).toEqual([]);
  });
});