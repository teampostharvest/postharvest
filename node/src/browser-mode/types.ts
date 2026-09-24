/**
 * Minimal structural subset of Playwright's browser surface used by the
 * browser-mode capture (finalplanv2.md §4 browser-mode).
 *
 * The real Playwright `Page`/`Response`/`Route` objects satisfy these shapes
 * (the adapter in playwright.ts casts them in); tests inject lightweight
 * fakes so the capture algorithm runs hermetically without a browser.
 */

export interface CapturePage {
  goto(
    url: string,
    opts: { waitUntil: "domcontentloaded"; timeout: number },
  ): Promise<void>;
  waitForTimeout(ms: number): Promise<void>;
  /** Current URL after navigation/redirects. */
  url(): string;
  content(): Promise<string>;
  evaluate<T>(fn: () => T): Promise<T>;
  on(event: "response", listener: (response: CaptureResponse) => void): void;
  querySelector(selector: string): Promise<CaptureElement | null>;
  click(selector: string): Promise<void>;
}

export interface CaptureElement {
  click(): Promise<void>;
}

export interface CaptureResponse {
  url(): string;
  text(): Promise<string>;
}

export interface CaptureRoute {
  request(): { resourceType(): string };
  abort(): Promise<void>;
  continue(): Promise<void>;
}

/** Browser session producing one page per capture; caller closes it. */
export interface CaptureSession {
  newPage(): Promise<CapturePage>;
  close(): Promise<void>;
  /**
   * RFC 6265 cookie lines present in the browser context *after* capture
   * (Slice C refresh).  FastAPI persists these back into the saved session
   * store; node never persists them (§2/§7).  Optional so hermetic fakes
   * can omit it — the route then reports `updated_cookies: []`.
   */
  dumpCookies?(): Promise<string[]>;
}

export interface BrowserOpenOptions {
  /** Single honest, fixed UA — mirrors http-mode (config.HONEST_USER_AGENT). */
  userAgent: string;
  viewport: { width: number; height: number };
  locale: string;
  launchArgs: string[];
  /** Abort image/media/font sub-resources while capturing (parity). */
  blockHeavyResources: boolean;
  /** Optional explicit chromium binary; undefined = Playwright's bundled one. */
  executablePath?: string;
  /** Optional Playwright channel (e.g. "chrome") to locate a system browser. */
  channel?: string;
  /**
   * Saved-session cookie lines (RFC 6265 "name=value; ...") to apply to the
   * browser context before navigation (FetchRequest.cookies, Slice C). Empty
   * / undefined = anonymous capture. Never persisted by node (§2/§7).
   */
  cookies?: string[];
}

/** Opens one capture browser session (the real one lives in playwright.ts). */
export type BrowserOpenFn = (opts: BrowserOpenOptions) => Promise<CaptureSession>;