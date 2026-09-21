import type { ReactNode } from "react";
import { findDocsPage } from "@/lib/docs-meta";

function P({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-ink-muted">{children}</p>;
}

function Li({ children }: { children: ReactNode }) {
  return <li className="text-sm leading-relaxed text-ink-muted">{children}</li>;
}

function Code({ children }: { children: ReactNode }) {
  return <code className="rounded-sm bg-bg-subtle px-1.5 py-0.5 text-[12px] text-ink">{children}</code>;
}

function Pre({ children }: { children: ReactNode }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-border bg-bg px-4 py-3 text-xs leading-relaxed text-ink-muted">
      {children}
    </pre>
  );
}

function H2({ children }: { children: ReactNode }) {
  return <h2 className="pt-2 text-sm font-semibold tracking-tight text-ink">{children}</h2>;
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-left text-xs">
        <thead className="bg-bg-subtle text-ink-muted">
          <tr>
            {head.map((h) => (
              <th key={h} className="whitespace-nowrap px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((cells, i) => (
            <tr key={i}>
              {cells.map((cell, j) => (
                <td key={j} className="whitespace-nowrap px-3 py-2 align-top text-ink-muted">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const BODIES: Record<string, ReactNode> = {
  overview: (
    <>
      <H2>What this is</H2>
      <P>
        PostHarvest extracts <strong className="text-ink">publicly available</strong> posts from
        Facebook pages and profiles and saves them to a local database. A background worker tracks progress per page,
        a dashboard lets you watch a run live, and results can be exported as JSON, CSV or Excel.
      </P>
      <H2>What it never does</H2>
      <P>
        It does not log in on your behalf, it does not answer CAPTCHAs, it does not rotate identities or proxies to
        evade blocks, and it never attempts content behind authentication or privacy controls. Requests are throttled
        and time-boxed by design.
      </P>
      <H2>Stack</H2>
      <Table
        head={["Layer", "Technology", "Purpose"]}
        rows={[
          [<span key="b">Backend</span>, <Code key="b">FastAPI + SQLAlchemy</Code>, "Job workers, scraping, DB, exports"],
          [<span key="s">Scraper</span>, <Code key="s">requests / Playwright</Code>, "Static HTML fallback or live GraphQL feed"],
          [<span key="f">Frontend</span>, <Code key="f">Next.js</Code>, "Dashboard, history, docs"],
          [<span key="d">Data</span>, <Code key="d">SQLite (default)</Code>, "Jobs, sources, posts, media, errors"],
        ]}
      />
      <H2>The job model</H2>
      <P>
        Every run is a <Code>scrape_job</Code> that moves <Code>queued → running → completed | failed</Code>. It can be
        paused and resumed, and cancelled through deletion. Progress counters (pages total/completed, posts found,
        posts processed, duplicates, errors) are persisted so a dashboard can resume polling at any time.
      </P>
    </>
  ),

  "getting-started": (
    <>
      <H2>Check the prerequisites</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>Use Python 3.14+ in an isolated virtual environment (the project uses <Code>.venv</Code>).</Li>
        <Li>Use Node.js 20+ for the dashboard (Next.js).</Li>
        <Li>Expect a local SQLite database to be created automatically in <Code>./data</Code>.</Li>
      </ul>
      <H2>Create the virtual environment</H2>
      <Pre>{`python -m venv .venv
source .venv/bin/activate
pip install -e .`}</Pre>
      <H2>Install the frontend</H2>
      <Pre>{`cd frontend
npm install`}</Pre>
      <H2>Install browser extras (optional)</H2>
      <P>
        Run browser mode only after you install Playwright and its Chromium:
      </P>
      <Pre>{`pip install playwright
playwright install chromium`}</Pre>
      <P>
        Non-browser scraping needs only <Code>requests</Code> and <Code>beautifulsoup4</Code>.
      </P>
      <H2>Know the layout</H2>
      <Pre>{`backend/
  api/        # FastAPI routers (scrape, jobs, exports, accounts, health)
  core/       # config, database, exceptions, job manager
  scraper/    # browser_scraper, parser, normalizer
  models/     # SQLAlchemy ORM models
  schemas/    # Pydantic contracts
  services/   # job service, stats, serialization, crawl state
frontend/
  app/        # Next.js App Router (shell, pages, docs)
  components/ # UI components
  lib/        # API client, utils, types, docs registry, settings
tests/        # pytest suites (API, state machine, exports, e2e)
data/         # sqlite DB, cookies, credentials index, exports`}</Pre>
    </>
  ),

  quickstart: (
    <>
      <H2>1. Start the backend</H2>
      <Pre>{`uvicorn backend.main:app --host 127.0.0.1 --port 8000`}</Pre>
      <H2>2. Start the dashboard</H2>
      <Pre>{`cd frontend
npm run dev   # http://localhost:3000`}</Pre>
      <H2>3. Run your first scrape</H2>
      <P>
        Paste a public page URL into the New scrape form, set a time frame to limit the range, and press Start
        Scraping. Watch the live progress bar as the worker walks each source, then open any completed run&lsquo;s
        posts from the History page.
      </P>
      <H2>4. Export the results</H2>
      <P>
        When the run completes, download the dataset from the <Code>Export results</Code> card — the three buttons
        stream the file live through your session. Use <Code>JSON</Code> for machine processing, <Code>CSV</Code>{" "}
        for spreadsheets, and <Code>Excel</Code> for a formatted workbook.
      </P>
      <H2>5. Enable browser mode (optional)</H2>
      <P>
        Balanced caps apply: the static-HTML route yields about five posts per page. To collect more, tick{" "}
        <Code>Browser mode</Code> and optionally pick a saved session under <Code>Saved accounts</Code>. The Playwright
        scraper reads the page&lsquo;s live GraphQL feed and scrolls for more posts.
      </P>
      <H2>Reading the dashboard</H2>
      <P>
        The UI adds a hint line only when it changes what you should do next: the ~5-post cap note when you enable
        Browser mode without a saved account, the <Code>larger than the preview cap</Code> note when a dataset exceeds
        2,000 posts, and the thin loading / empty states. It deliberately omits lines that would mislead you — it never
        prints a fabricated count, never lists a duplicate as a new post, and never draws a progress bar that did not
        come from the server&lsquo;s most recent poll. When a field is absent, the row shows an em dash, not a guess.
      </P>
    </>
  ),

  cli: (
    <>
      <H2>Entry point</H2>
      <Pre>{`python cli.py <command> [options]`}</Pre>
      <Table
        head={["Command", "What it does"]}
        rows={[
          [<Code key="s">scrape</Code>, "Run a scrape from the terminal with a URL list or a file"],
          [<Code key="n">normalize</Code>, "Re-run the normalize step over stored browser results"],
          [<Code key="l">login</Code>, "Open a visible browser to save Facebook session cookies"],
          [<Code key="a">accounts</Code>, "List saved accounts and their cookie files"],
          [<Code key="e">export</Code>, "Export a stored job to json/csv/xlsx"],
        ]}
      />
      <H2>Scrape with flags</H2>
      <Pre>{`python cli.py scrape --urls "https://www.facebook.com/page1" \\
                  --urls "https://www.facebook.com/page2" \\
                  --max-posts 200 --post-type video --browser --scrolls 60 --export csv`}</Pre>
      <ul className="list-disc space-y-1 pl-5">
        <Li>Pass <Code>--urls</Code> once per URL, or point at a file with the URL list.</Li>
        <Li>Add <Code>--browser</Code> to read the live GraphQL feed instead of static HTML.</Li>
        <Li>Rotate sessions across URLs with a comma-separated <Code>--account a,b</Code>: account A for URL 1, account B for URL 2, and so on.</Li>
      </ul>
      <H2>Exit codes</H2>
      <P>
        The process exits <Code>0</Code> on full success, <Code>1</Code> when a source failed but others completed, and{" "}
        <Code>2</Code> for usage or environment errors. Per-URL failures never abort the whole run.
      </P>
    </>
  ),

  "browser-mode": (
    <>
      <H2>Why browser mode</H2>
      <P>
        The default scraper reads the page&lsquo;s rendered HTML, which Facebook throttles to a small number of posts
        (about 5) for anonymous visitors. Browser mode drives a real Chromium via Playwright to load the page&lsquo;s own
        GraphQL feed and scroll through it, collecting far more posts — including posts rendered only after the timeline
        tab is clicked.
      </P>
      <H2>How it works</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>Injects saved cookies, blocks images/media/fonts to keep the page fast, and dismisses cookie/login popups.</Li>
        <Li>Detects login walls up front by probing for the email/login form — and stops that source without retries.</Li>
        <Li>Clicks the timeline tab, then scrolls in rounds (<Code>scrolls</Code>, default 40) with a 1.5s delay between rounds.</Li>
        <Li>Snapshots each round and accumulates unique post cards: Facebook virtualizes the feed, keeping only a few cards mounted, so a single final snapshot would lose everything scrolled past.</Li>
        <Li>Captures GraphQL responses that carry embedded feed payloads.</Li>
        <Li>Merges snapshot + payload posts, then normalizes and dedups.</Li>
      </ul>
      <H2>Config</H2>
      <P>
        In the dashboard, tick <Code>Browser mode (Playwright)</Code>. Optionally provide a saved account under{" "}
        <Code>Accounts</Code> — anonymous sessions stay capped, cookies unlock the full feed.
      </P>
      <H2>Safety defaults</H2>
      <P>
        Browser runs are throttled and capped: scroll delay, a maximum number of scroll rounds (<Code>300</Code>), and a
        hard per-source post cap all apply. Cancel a run anytime from the dashboard or with the job delete endpoint; the
        worker checks cancellation between sources.
      </P>
    </>
  ),

  graphql: (
    <>
      <H2>Why: the anonymous-HTML wall</H2>
      <P>
        Facebook serves anonymous visitors only the five most recent public posts — a server-side cap, not a scraper
        limit. The <Code>www</Code> variant renders just <Code>og:*</Code> metadata plus JavaScript to that traffic, so
        post text and engagement never reach the static markup. The <Code>mbasic</Code> and mobile variants once
        rendered full lists, but Meta is retiring them and their markup changes constantly. On top of that, the DOM
        structure itself keeps shifting (<Code>&lt;article&gt;</Code> → <Code>div[role="article"]</Code> →{" "}
        <Code>data-ad-preview</Code> → <Code>data-ft</Code>), so a parser that depends on any single structure breaks
        whenever Meta ships a change.
      </P>
      <P>
        The result: honest extraction from plain HTML alone returns little data. That is expected behavior — the
        scraper records only what the page actually renders (see <Code>quirks</Code>).
      </P>

      <H2>The turn: observe the page&lsquo;s own feed</H2>
      <P>
        The public page you open in a browser is a single-page app. It loads each batch of stories through{" "}
        <Code>POST /api/graphql/</Code> — the Comet feed — instead of swapping the DOM. The DOM keeps only a few
        mounted cards at a time (virtualization), so grabbing <Code>page.content()</Code> at the end loses everything
        scrolled out of view.
      </P>
      <ul className="list-disc space-y-1 pl-5">
        <Li>
          Playwright routes every network response through a handler. When a response ends in{" "}
          <Code>/api/graphql/</Code> and its body carries both <Code>post_id</Code> and <Code>creation_time</Code>,
          the scraper captures it.
        </Li>
        <Li>It diffs the returned post IDs against the ones already captured and appends only new posts.</Li>
        <Li>Each payload is re-embedded into the page snapshot as a <Code>{"<script data-fb-graphql-feed='1'>"}</Code> block.</Li>
      </ul>
      <P>
        This keeps the pipeline stateless: the parser reads one HTML document, exactly as it does for anonymous pages,
        and captures whatever the page itself loaded for the user — never content behind authentication.
      </P>

      <H2>How the parser reads it</H2>
      <P>
        A single <Code>/api/graphql/</Code> response is frequently several JSON documents serially concatenated (one
        per query or mutation result). The parser iterates those documents, then flattens story entries from two shapes:
      </P>
      <ul className="list-disc space-y-1 pl-5">
        <Li><Code>data.node</Code> entries where <Code>__typename</Code> is <Code>Story</Code>.</Li>
        <Li>The <Code>data.node.timeline_list_feed_units.edges[].node</Code> list.</Li>
      </ul>
      <P>
        Each story node becomes a parsed post carrying <Code>post_id</Code>, <Code>creation_time</Code>, the rendered{" "}
        <Code>message.text</Code>, media attachments and engagement counts. Permalinks are rebuilt as{" "}
        <Code>permalink.php?story_fbid=…&amp;id=…</Code>.
      </P>

      <H2>Merge priority</H2>
      <P>
        Posts are merged in descending richness: GraphQL stories first (the richest data), then embedded-script posts
        whose IDs are not yet covered, then DOM posts not yet seen. The final pass deduplicates by <Code>post_id</Code>,
        first occurrence wins. GraphQL complements the DOM and script heuristics — it never replaces them, so anonymous
        pages keep working without a browser.
      </P>

      <H2>What it does and does not unlock</H2>
      <P>
        Browser mode unlocks exact timestamps, full message text, media attachments and engagement counts — for the
        posts the page itself loaded for that session. Cookies (a saved session) turn the anonymous cap into the full
        feed; an anonymous browser run stays capped just like anonymous HTML.
      </P>
      <P>
        It still honors the user&lsquo;s limit boundaries: reaction breakdowns are usually not public (they stay{" "}
        <Code>None</Code>), video file URLs require authenticated JavaScript or the Graph API, transcripts are never
        public, age-gated posts raise <Code>auth_required</Code>, and traffic checks raise <Code>rate_limited</Code>.
      </P>

      <H2>Try it yourself</H2>
      <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-relaxed text-ink-muted">
        <li>Save a session once: <Code>python cli.py login --account default</Code>.</li>
        <li>Start a browser scrape: tick <Code>Browser mode</Code> in the dashboard, or pass <Code>--browser --scrolls 60</Code> on the CLI.</li>
        <li>Watch the live progress bar, then open the completed run&lsquo;s posts.</li>
        <li>Compare the post count with a normal-mode scrape of the same page to see the cap difference.</li>
      </ol>
    </>
  ),

  sessions: (
    <>
      <H2>Anonymous cap</H2>
      <P>
        Without a session, Facebook shows roughly the five most recent public posts to an anonymous visitor. That is a
        server-side limit, not a scraper bug — the parser records everything the page actually renders.
      </P>
      <H2>Saving a session</H2>
      <P>
        Two kinds of sessions exist. Operator-managed sessions are shared by every user and saved from the CLI:
      </P>
      <Pre>{`python cli.py login --account default`}</Pre>
      <P>
        Your own personal sessions are added in the dashboard instead: <Code>Saved sessions → Add my session</Code>.
        That starts a one-time login browser and hands you a link — you open it in a new tab and sign in to Facebook
        there (solving any CAPTCHA yourself). The app only captures the resulting session cookie: the password is never
        stored, and your plan caps how many sessions you can keep. Operators can also add sessions shared by everyone
        with <Code>Add shared session</Code> (hidden for regular users).
      </P>
      <P>
        Cookie files remain on disk under <Code>data/</Code> for the operator pool, or{" "}
        <Code>data/personal/&#123;user&#125;/</Code> for personal sessions; an encrypted mirror row is kept in the
        database as well, so a restored host still lists its sessions. A credentials index keeps only metadata.
      </P>
      <H2>Using sessions</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>Dashboard: enable Browser mode and pick the session from the <Code>Saved account</Code> dropdown —{" "}
          <Code>me/</Code> entries are your own sessions, <Code>ops/</Code> are operator-managed and shared.</Li>
        <Li>CLI: pass <Code>--account</Code>, or comma-separate several to rotate across URLs.</Li>
        <Li>
          List sessions with <Code>python cli.py accounts</Code>; review or remove them on the{" "}
          <Code>Saved accounts</Code> page.
        </Li>
      </ul>
      <H2>Renew expiring sessions</H2>
      <P>
        Saved cookies typically last one to two weeks. Relog when a run returns <Code>auth_required</Code>: the login
        flow polls for the <Code>c_user</Code> session cookie to confirm success, then rewrites the cookie file.
      </P>
      <H2>API</H2>
      <P>
        <Code>GET /api/accounts</Code> returns session metadata split into the shared <Code>ops</Code> pool and your
        <Code>me</Code> sessions; <Code>DELETE /api/accounts/&#123;scope&#125;/&#123;name&#125;</Code> removes one.
        <Code>POST /api/accounts/capture</Code> starts the live login flow and returns the tab link ({" "}
        <Code>ops</Code> scope requires the ops role); <Code>DELETE /api/accounts/capture/&#123;id&#125;</Code> aborts
        it. Cookie contents are never exposed over the API.
      </P>
    </>
  ),

  options: (
    <>
      <H2>Supported filters</H2>
      <Table
        head={["Option", "Type", "Default", "Notes"]}
        rows={[
          [<Code key="u">urls</Code>, "array", "required", "1..10 public FB page/profile URLs"],
          [<Code key="m">max_posts</Code>, "int", "null", "global cap across all sources"],
          [<Code key="s">start_date</Code>, "YYYY-MM-DD", "null", "inclusive, UTC"],
          [<Code key="e">end_date</Code>, "YYYY-MM-DD", "null", "inclusive, UTC"],
          [<Code key="t">post_type</Code>, "text|image|video|link|all", "all", "case-insensitive"],
          [<Code key="sc">scrolls</Code>, "int", "40", "browser scroll rounds, max 300"],
          [<Code key="a">account</Code>, "string", "null", "saved session for browser mode"],
        ]}
      />
      <H2>Time frames on the dashboard</H2>
      <P>
        Presets (last 7/30/90 days, last year) set the start/end dates for you; picking custom dates clears the preset.
        The <Code>Settings</Code> page lets you persist default <Code>max_posts</Code>, <Code>post_type</Code>,{" "}
        <Code>scrolls</Code> and <Code>browser mode</Code> that prefill the form.
      </P>
      <H2>Post types</H2>
      <P>
        Filters compare against the normalized <Code>post_type</Code>: <Code>text</Code>, <Code>image</Code>,{" "}
        <Code>video</Code>, <Code>link</Code>. Posts whose type cannot be classified are treated as <Code>text</Code>.
      </P>
    </>
  ),

  data: (
    <>
      <H2>Normalized post model</H2>
      <P>
        Every extracted post becomes a flat, nullable-aware record: identity (post id, page, URL), content (text,
        caption, hashtags, mentions, links), engagement (likes, reactions by type, comments, shares, views) and media
        (thumbnail, video URL, transcript). Missing values are <Code>null</Code>, never fabricated.
      </P>
      <H2>Deduplication</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>In-memory dedup keyed by post id across sources in one run.</Li>
        <Li>Database-level unique constraint per source as a second line of defence.</Li>
        <Li>
          Duplicates are counted in the <Code>duplicates</Code> counter without overwriting the stored originals.
        </Li>
      </ul>
      <H2>Storage</H2>
      <P>
        Rows live in <Code>posts</Code>, <Code>media</Code>, <Code>engagement_metrics</Code> and{" "}
        <Code>scrape_sources</Code> tables. Deleting a job removes all of its rows via cascade.
      </P>
      <H2>Trust boundary</H2>
      <P>
        Facebook content is untrusted input: all text is rendered as plain text everywhere in the UI, and URLs pass
        through a strict <Code>http(s)</Code> validator before becoming links.
      </P>
    </>
  ),

  exports: (
    <>
      <H2>Formats</H2>
      <Table
        head={["Format", "Structure", "Best for"]}
        rows={[
          [<Code key="j">JSON</Code>, "full nested tree", "machine processing"],
          [<Code key="c">CSV</Code>, "one row per post (flat)", "spreadsheets"],
          [<Code key="x">Excel</Code>, "Posts / Engagement / Media / Metadata sheets", "formatted reports"],
        ]}
      />
      <H2>Requirements</H2>
      <P>
        Excel export needs <Code>openpyxl</Code>; CSV is stdlib-only. If <Code>openpyxl</Code> is missing the API
        returns <Code>{"503 { \"A dependency is unavailable\" }"}</Code> for Excel and the dashboard button reports the
        error.
      </P>
      <H2>Streaming & limits</H2>
      <P>
        Exports are generated server-side and streamed as attachments. They are not capped at 2,000 rows the way the
        in-browser preview is, so use the export endpoints for large datasets. A running job returns{" "}
        <Code>409</Code> until it finishes. The dashboard downloads are signed with your session and saved as{" "}
        <Code>facebook_posts.json</Code>, <Code>facebook_posts.csv</Code> or <Code>facebook_posts.xlsx</Code>.
      </P>
      <H2>Pruning results: delete</H2>
      <P>
        <Code>DELETE /api/jobs/&#123;id&#125;</Code> cancels (best-effort) and deletes a run and all its rows.
      </P>
    </>
  ),

  "rate-limits": (
    <>
      <H2>Design principles</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>Throttling is on by default; there is no "speed" flag.</Li>
        <Li>One request at a time per source sweep; the browser mode adds fixed scroll delays.</Li>
        <Li>Runs are time-boxed and cancellable, and per-URL failures degrade gracefully instead of retrying hot.</Li>
      </ul>
      <H2>Anonymous scraping etiquette</H2>
      <P>
        Facebook rate-limits anonymous visitors. Keep <Code>max_posts</Code> modest, prefer saved sessions for large
        pages, and avoid hammering the same page in back-to-back runs.
      </P>
      <H2>Your obligations</H2>
      <P>
        Only scrape pages you are authorized to access. Respect the site&lsquo;s terms, robots and access restrictions,
        and applicable law — especially data-protection rules. This tool&lsquo;s compliance posture is your legal
        posture: public data, throttled, never behind auth.
      </P>
      <H2>No identity rotation</H2>
      <P>
        The scraper never rotates user agents or IPs and never solves CAPTCHAs. If the site asks you to prove you are
        human, the job fails gracefully for that URL — that is expected, safe behavior.
      </P>
    </>
  ),

  "api-scrape": (
    <>
      <H2><Code>POST /api/scrape</Code></H2>
      <P>Validates the submitted URLs and queues a background job. Returns <Code>201</Code>.</P>
      <Pre>{`{ "urls": ["https://www.facebook.com/example"],
  "max_posts": 100,
  "start_date": "2026-09-01",
  "end_date": "2026-09-13",
  "post_type": "text",
  "scrolls": 40,
  "account": null }
→ 201 { "job_id": "…", "status": "queued" }`}</Pre>
      <H2>Validation</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>1..10 URLs, each a valid <Code>http(s)</Code> Facebook page/profile URL.</Li>
        <Li>2..300 <Code>scrolls</Code>; <Code>max_posts</Code> &ge; 1; dates <Code>YYYY-MM-DD</Code> with start &le; end.</Li>
        <Li><Code>post_type</Code> must be one of <Code>text|image|video|link|all</Code>.</Li>
        <Li>Invalid URLs are rejected with a <Code>400</Code> envelope (whole batch).</Li>
      </ul>
    </>
  ),

  "api-jobs": (
    <>
      <H2>Status & progress</H2>
      <Pre>{`GET /api/jobs/{job_id}
→ { "job_id": "…", "status": "running",
    "pages_total": 2, "pages_completed": 1,
    "posts_found": 120, "posts_processed": 60,
    "duplicates": 2, "errors": 0,
    "error_details": [], "created_at": "…" }`}</Pre>
      <H2>Lifecycle endpoints</H2>
      <Table
        head={["Endpoint", "Effect"]}
        rows={[
          [<Code key="p">POST /api/jobs/&#123;id&#125;/pause</Code>, "Pause a running job"],
          [<Code key="r">POST /api/jobs/&#123;id&#125;/resume</Code>, "Resume a paused job"],
          [<Code key="d">DELETE /api/jobs/&#123;id&#125;</Code>, "Cancel (best-effort) and delete (204)"],
        ]}
      />
      <H2>History</H2>
      <Pre>{`GET /api/jobs?page=1&page_size=25
→ { "items": [ { "job_id": "…", "status": "completed",
      "urls": ["https://…"], "max_posts": 50,
      "posts_processed": 50, "created_at": "…", "completed_at": "…" } ],
    "total": 3, "page": 1, "page_size": 25 }`}</Pre>
      <P>
        Unknown jobs return <Code>404</Code> with the standard error envelope. Pausing is best-effort: the worker only
        checks between sources.
      </P>
    </>
  ),

  "api-posts": (
    <>
      <H2>Paginated posts</H2>
      <Pre>{`GET /api/jobs/{job_id}/posts?page=1&page_size=50
→ { "items": [ { "post_id": "…", "post_url": "…",
      "page_name": "Example", "published_at": "…",
      "text": "…", "likes": 42, "comments_count": 7,
      "shares": 3, "views_count": 900,
      "media_url": "…", "post_type": "video" } ],
    "total": 120, "page": 1, "page_size": 50 }`}</Pre>
      <P>
        Posts are newest-first by <Code>published_at</Code>. <Code>page_size</Code> is capped at the server maximum
        (200). The <Code>items</Code> array is empty for jobs with no extracted posts and all fields are nullable.
      </P>
      <H2>Stats</H2>
      <Pre>{`GET /api/jobs/{job_id}/stats
→ { "job_id": "…", "total_posts": 50, "total_likes": 1234,
    "total_reactions": 1400, "total_comments": 88,
    "total_shares": 21, "total_views": 3000,
    "videos": 12, "images": 28, "links": 4, "texts": 6,
    "first_post_at": "…", "last_post_at": "…" }`}</Pre>
    </>
  ),

  "api-exports": (
    <>
      <H2>Exports</H2>
      <Pre>{`GET /api/jobs/{job_id}/export/json
GET /api/jobs/{job_id}/export/csv
GET /api/jobs/{job_id}/export/excel`}</Pre>
      <Table
        head={["Code", "Meaning"]}
        rows={[
          [<Code key="200">200</Code>, "streamed file download (Content-Disposition attachment)"],
          [<Code key="404">404</Code>, "unknown job"],
          [<Code key="409">409</Code>, "job still running"],
          [<Code key="503">503</Code>, "Excel dependency (openpyxl) unavailable"],
        ]}
      />
      <P>
        Every export endpoint requires the same Firebase bearer token as the rest of the API. The dashboard&lsquo;s
        Export card fetches the file with your session and saves it with the canonical filename — a plain browser
        navigation to the URL without the token returns <Code>401</Code>.
      </P>
      <H2>Accounts</H2>
      <Pre>{`GET /api/accounts
→ { "items": [ { "name": "default",
      "cookies_file": "fb_cookies.json",
      "saved_at": "2026-09-13T12:00:00Z" } ],
    "total": 1 }

DELETE /api/accounts/{name}   → 204`}</Pre>
      <P>
        The accounts endpoints expose metadata only; cookie contents never leave the server.
      </P>
    </>
  ),

  quirks: (
    <>
      <H2>Honesty over completeness</H2>
      <P>
        The parser&lsquo;s contract is simple: if the markup does not carry a value, the field is <Code>None</Code> —
        never fabricated, never guessed. This applies to every post field, every counter and every URL. It also never
        reads a number from the wrong place: per-metric counts are looked up within about twelve characters of their
        label (<Code>"3 Comments"</Code>, <Code>"1.2K views"</Code>, <Code>"All reactions: 15"</Code>) so unrelated
        numbers in a post body never leak into the wrong metric.
      </P>

      <H2>The counting invariant</H2>
      <P>
        Every source keeps one invariant across all stages:
      </P>
      <Pre>{`posts_discovered == posts_extracted + posts_skipped + posts_failed`}</Pre>
      <P>
        <Code>duplicates_removed</Code> is deliberately separate: each removed duplicate is also counted inside{" "}
        <Code>posts_skipped</Code>, so the discovered total never double-counts. Missing field values never count as
        failures; they simply stay <Code>None</Code>.
      </P>

      <H2>Timestamps</H2>
      <P>
        Parse timestamps in this priority order: <Code>data-utime</Code> epoch values (exact), relative strings like{" "}
        <Code>"Just now"</Code>, <Code>"3 h"</Code>, <Code>"2 d"</Code> or <Code>"1 w ago"</Code>, then absolute strings
        like <Code>"August 2, 2024 at 8:00 AM"</Code>, <Code>"August 2 at 8:00 AM"</Code>,{" "}
        <Code>"Yesterday at 5:30 PM"</Code> and ISO 8601. Year-implied absolute strings roll back one year if the result
        is in the future. Naive (timezone-less) strings are stored as UTC by convention because public HTML does not
        reveal the viewer&lsquo;s timezone; when Facebook provides an epoch, the result is exact.
      </P>

      <H2>Counts and precision</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li><Code>k</Code>/<Code>m</Code> suffixes parse correctly (<Code>1.2K</Code> → <Code>1200</Code>).</Li>
        <Li>
          The shares pattern is precise (<Code>"X shares"</Code>), so a string like <Code>"Shared with Public 13m"</Code>{" "}
          never reads the relative time <Code>13m</Code> as 13,000,000 shares.
        </Li>
        <Li>
          Reaction breakdowns are frequently not rendered publicly — Facebook often shows reaction images without
          counts — so those fields stay <Code>None</Code>. That is documented best-effort behavior.
        </Li>
      </ul>

      <H2>Finding post containers</H2>
      <P>
        The parser locates containers tolerantly, because Facebook changes markup frequently. It walks this ladder and
        stops at the first structure that matches: <Code>&lt;article&gt;</Code>, <Code>div[role="article"]</Code>,{" "}
        <Code>div[data-ad-preview="message"]</Code>, elements carrying a <Code>data-ft</Code> payload, then legacy
        class-based markers. Nested candidates are dropped so a post is never parsed twice.
      </P>

      <H2>Media sanity</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>Skip placeholders, emoji/icon assets, profile pictures, images under 60×60 and link-card previews.</Li>
        <Li>Use the best <Code>src</Code>: explicit <Code>src</Code> when real, else <Code>data-src</Code> (lazy-loading).</Li>
        <Li>Fall back to <Code>og:image</Code> only when the post itself carries no image.</Li>
        <Li>
          <Code>video_url</Code> is usually <Code>None</Code> on public HTML — kept honest instead of guessed. Direct{" "}
          <Code>mp4</Code> metadata is rare.
        </Li>
      </ul>

      <H2>Link shims</H2>
      <P>
        Facebook wraps external URLs in a shim (<Code>l.facebook.com/l.php?u=…</Code>). The parser resolves the obvious{" "}
        <Code>u=</Code> parameter to the real external URL. That is pure URL parsing — not an evasion technique.
      </P>

      <H2>Markup drift</H2>
      <P>
        When a structure stops matching, the parser falls down the ladder instead of failing. A single bad post never
        crashes a source: it records the error and continues. If a whole page shifts to an unknown layout, the source
        reports its page-level error and the run records it per-URL.
      </P>
    </>
  ),

  errors: (
    <>
      <H2>The error envelope</H2>
      <Pre>{`{ "error": { "code": string, "message": string } }`}</Pre>
      <Table
        head={["HTTP", "code", "Typical cause"]}
        rows={[
          [<span key="400">400</span>, <Code key="c">bad_request</Code>, "invalid URLs, dates out of order, bad post_type"],
          [<span key="404">404</span>, <Code key="c">not_found</Code>, "unknown job or account"],
          [<span key="409">409</span>, <Code key="c">conflict</Code>, "export while running, or one job at a time"],
          [<span key="422">422</span>, <Code key="c">invalid_input</Code>, "malformed body (FastAPI validation)"],
          [<span key="500">500</span>, <Code key="c">internal_error</Code>, "unexpected backend failure"],
          [<span key="503">503</span>, <Code key="c">unavailable</Code>, "missing dependency (openpyxl) or scraper env"],
        ]}
      />
      <H2>Troubleshooting checklist</H2>
      <ul className="list-disc space-y-1 pl-5">
        <Li>A job shows <Code>failed</Code> for one URL but others completed: that is normal, per-URL isolation.</Li>
        <Li>Only ~5 posts per page in normal mode: switch to Browser mode or add a saved session.</Li>
        <Li>Excel export greys out: <Code>openpyxl</Code> is not installed; use JSON or CSV.</Li>
        <Li>Backend health: <Code>GET /api/health</Code> reports database status and version.</Li>
      </ul>
    </>
  ),
};

export function getDocsBody(slug: string): ReactNode | undefined {
  const page = findDocsPage(slug);
  if (!page) return undefined;
  return BODIES[page.slug];
}