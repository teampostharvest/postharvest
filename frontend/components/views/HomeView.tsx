"use client";

import { useState } from "react";
import { MotionIcon } from "motion-icons-react";
import { isFacebookUrl } from "@/lib/api";

export interface HomeViewProps {
  /** Receives a normalized target URL to hand off to the Investigation screen. */
  onTrace: (url: string) => void;
  /** Opens the batch/list view (Hands the user off to Investigation). */
  onAdvanced?: () => void;
}

/**
 * Editorial, left-aligned landing hero in the high-end technical-document
 * style: a massive sans header, one sentence, and an inline URL query bar
 * that pipes the target through to the Investigation screen.
 */
export function HomeView({ onTrace, onAdvanced }: HomeViewProps) {
  const [url, setUrl] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const submit = () => {
    const check = isFacebookUrl(url);
    if (!check.valid || !check.normalized) {
      setValidationError(check.reason ?? "Enter a valid Facebook page or profile URL.");
      return;
    }
    setValidationError(null);
    onTrace(check.normalized);
  };

  return (
    <div className="mx-auto w-full max-w-5xl px-8 pt-24">
      <h1 className="mb-6 font-display text-3xl font-semibold tracking-tight text-ink leading-tight">
        Facebook scraping,
        <br />
        fully automated.
      </h1>
      <p className="mb-12 max-w-2xl text-lg leading-relaxed text-ink-muted">
        Drop a target URL. The system automatically scrapes public feeds, profiles, and media, folding every hit into a
        structured JSON report.
      </p>

      <div className="flex w-full max-w-4xl items-stretch rounded-lg border border-border-strong bg-bg-elevated shadow-sm">
        <input
          id="master-url"
          type="text"
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
            if (validationError) setValidationError(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") submit();
          }}
          placeholder="https://www.facebook.com/target..."
          autoComplete="off"
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent px-6 py-5 font-mono text-sm text-ink placeholder:text-ink-faint focus:outline-hidden"
        />
        <button
          type="button"
          onClick={submit}
          aria-label="Scrape target"
          className="btn-sheen flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-r-full bg-accent px-8 text-sm font-medium text-accent-ink transition-all duration-200 hover:bg-accent-hover hover:shadow-lg active:scale-[0.98]"
        >
          <MotionIcon name="Play" size={16} aria-hidden="true" animation="pop" trigger="hover" />
          <span>Scrape target</span>
        </button>
      </div>

      <p className="mt-3 min-h-[1em] text-xs text-danger" aria-live="polite">
        {validationError}
      </p>

      <button
        type="button"
        onClick={onAdvanced}
        className={`mt-4 block text-sm font-medium text-highlight transition-colors ${
          onAdvanced ? "cursor-pointer hover:text-ink" : "cursor-default"
        }`}
      >
        Or just do it in batch →
      </button>
    </div>
  );
}