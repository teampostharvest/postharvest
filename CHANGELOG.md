# [1.6.0](https://github.com/teampostharvest/postharvest/compare/v1.5.3...v1.6.0) (2026-09-24)


### Bug Fixes

* **obs:** flat alerting provisioning layout + cadvisor healthcheck URL ([481b99b](https://github.com/teampostharvest/postharvest/commit/481b99b98028c79938c16ac0e5e476c7c86bc8af))
* **obs:** keep Grafana alerting provisioning valid when webhook URL is unset ([3d799dc](https://github.com/teampostharvest/postharvest/commit/3d799dc2044a630728a8cdc8269c8e8d09f72fe3))
* **obs:** use canonical oliver006/redis_exporter image for the redis metrics exporter ([f8cf07e](https://github.com/teampostharvest/postharvest/commit/f8cf07e53f51d7e2c19ae680f332ee586d01c355))


### Features

* **backend:** node feed-walk seam behind the GUEST_FEED_WALK flag ([9e17a87](https://github.com/teampostharvest/postharvest/commit/9e17a879f90c1f0ebd445bc01f6cfc3486512f82))
* **node:** crawlee feed-frame transport with session-pool rotation ([05a91ed](https://github.com/teampostharvest/postharvest/commit/05a91ed7d4ad1f7d503764fbd583d6642efbb6cd))
* **obs:** monitoring platform — prometheus, grafana, exporters, dashboards, alerting (D13–D17) ([65c791c](https://github.com/teampostharvest/postharvest/commit/65c791c0ec2459ca7713aadac55992a7d62cb2f1))

## [1.5.3](https://github.com/teampostharvest/postharvest/compare/v1.5.2...v1.5.3) (2026-09-24)


### Bug Fixes

* **obs:** keep Grafana alerting provisioning valid when webhook URL is unset ([14985bd](https://github.com/teampostharvest/postharvest/commit/14985bde666fbe291db00eef72d68b0ac00dbe1f))

## [1.5.2](https://github.com/teampostharvest/postharvest/compare/v1.5.1...v1.5.2) (2026-09-24)


### Bug Fixes

* **obs:** flat alerting provisioning layout + cadvisor healthcheck URL ([a1fca4f](https://github.com/teampostharvest/postharvest/commit/a1fca4f0640a83ad65e05af732f130ef599a5d6f))

## [1.5.1](https://github.com/teampostharvest/postharvest/compare/v1.5.0...v1.5.1) (2026-09-24)


### Bug Fixes

* **obs:** use canonical oliver006/redis_exporter image for the redis metrics exporter ([ff69c0c](https://github.com/teampostharvest/postharvest/commit/ff69c0c0d1f3b81bb04c334d3b23d6d41067932a))

# [1.5.0](https://github.com/teampostharvest/postharvest/compare/v1.4.0...v1.5.0) (2026-09-24)


### Features

* **obs:** monitoring platform — prometheus, grafana, exporters, dashboards, alerting (D13–D17) ([da4f852](https://github.com/teampostharvest/postharvest/commit/da4f852556c7dce00b6f74273dd33575d86dd200))

# [1.4.0](https://github.com/teampostharvest/postharvest/compare/v1.3.0...v1.4.0) (2026-09-24)


### Bug Fixes

* **capture:** push-stream viewer + session seeding so live capture completes fast ([c15139c](https://github.com/teampostharvest/postharvest/commit/c15139ca3f2594adf6e269b29e0442fc929db184))


### Features

* **obs:** metrics endpoints on backend, go and node (observability, part 1) ([480c57f](https://github.com/teampostharvest/postharvest/commit/480c57f0f6a76dd4401a21989991fa20ea5f178a))

# [1.3.0](https://github.com/teampostharvest/postharvest/compare/v1.2.0...v1.3.0) (2026-09-24)


### Bug Fixes

* **api:** cache identity and accounts, rebalance pools against exhaustion ([8c417b3](https://github.com/teampostharvest/postharvest/commit/8c417b3f05d5cd86a7d9942961ae44d2812a7c1a))
* **api:** stop postgres pool exhaustion on remote reads ([69a8650](https://github.com/teampostharvest/postharvest/commit/69a865071d7f5581fc49f7cff2220df93029c944))
* **golang:** vendor redis maintnotifications/logs for offline build ([7d434d4](https://github.com/teampostharvest/postharvest/commit/7d434d4a071c72da5074de08580957dc22829baa))
* **test:** flush passive effects before Escape in HoverPeekPanel dismissal test ([10cb4df](https://github.com/teampostharvest/postharvest/commit/10cb4df78dd10ac2f32b568aceb6a3716444fddb))


### Features

* **accounts:** import sessions from cookies.txt paste ([efeb5db](https://github.com/teampostharvest/postharvest/commit/efeb5db846efd303422203f1c31d63fbb2caa3c5))
* **browser-seam:** route browser scrapes through node behind USE_NODE_BROWSER ([31f4bf8](https://github.com/teampostharvest/postharvest/commit/31f4bf8f64cb8681ba9632704c691db52f8ddc2e))
* **cache:** production arming path for runtime TTL consult seam ([6b0fd17](https://github.com/teampostharvest/postharvest/commit/6b0fd17fe5f5b7525bca0638831e5ec012162259))
* **contract:** add browser-mode fields to FetchRequest/FetchResponse ([2eac010](https://github.com/teampostharvest/postharvest/commit/2eac010ec8cd6b83c5cf698951b4fe3cb7a642ac))
* **contract:** add canonical postharvest proto and fixtures ([04c0479](https://github.com/teampostharvest/postharvest/commit/04c047996e8a6429b4400df77d26c1d70f0ef72f))
* **db:** size postgres client pool by process role ([85bfb61](https://github.com/teampostharvest/postharvest/commit/85bfb6195085e6a4a0db1250b6a388dda3fdd653))
* **jobs:** add arq execution seam and out-of-process worker ([e4dea54](https://github.com/teampostharvest/postharvest/commit/e4dea54ddd9564d4af9477041f6f9475b40ab014))
* **jobs:** externalize job-state to redis (phase 1 part 1) ([008fb8c](https://github.com/teampostharvest/postharvest/commit/008fb8c40c8f230c35524315e24853e9b4a1df8c))
* **node-fetcher:** scaffold Fastify fetch service with http mode ([4b6a0eb](https://github.com/teampostharvest/postharvest/commit/4b6a0ebac981715ab9761dc67e7c3a6b56db25bc))
* **node:** add HTTP fetch seam behind flag and rename node-fetcher to node ([def9141](https://github.com/teampostharvest/postharvest/commit/def914149b1751e21cd92dc7edb3069a9210bb8e))
* **node:** browser-mode capture via Playwright with session cookies ([552a38c](https://github.com/teampostharvest/postharvest/commit/552a38ce055aa0e0dc60727f4ed0909988a67fba))
* **node:** discover system Chromium in production browser open ([66ab7b0](https://github.com/teampostharvest/postharvest/commit/66ab7b0e9eac99f37736343b56222dbbc6cc889a))
* **node:** slice C refreshed-session write-back + hermetic seam (incl. falsy persist-gate closures) ([08126da](https://github.com/teampostharvest/postharvest/commit/08126da25003ceafcdfdd59c0737ea2fbf49e860))
* seal slices D/B/C/A — Redis intel, mirror fallback, TTL cache, Go worker ([da9da95](https://github.com/teampostharvest/postharvest/commit/da9da956dbdfe29a6217052547b7e208870ee36a))
* **usage:** top-bar usage pill + expandable popover (theme tokens, AA contrast) ([6b15e72](https://github.com/teampostharvest/postharvest/commit/6b15e720a960c7947675c2b15ffbf4ba71d96b8c))

# [1.2.0](https://github.com/teampostharvest/postharvest/compare/v1.1.0...v1.2.0) (2026-09-21)


### Bug Fixes

* **frontend:** load avatar from firebase user with initials fallback ([1264a81](https://github.com/teampostharvest/postharvest/commit/1264a8140e7cd793791db2ae3eb7316ad851f3a1))


### Features

* **frontend:** overhaul dashboard shell with ops rail and usage api ([c212e3b](https://github.com/teampostharvest/postharvest/commit/c212e3b53636b71eea6bccdb6ed82cdb0df308df))

# [1.1.0](https://github.com/teampostharvest/postharvest/compare/v1.0.1...v1.1.0) (2026-09-17)


### Bug Fixes

* **auth:** handle PEM formatting for Firebase Admin SDK private key and fallback gracefully ([d6c3747](https://github.com/teampostharvest/postharvest/commit/d6c374704cc3106eef9e082b66012f87a57435bf))
* **docker:** add prod nginx TLS and frontend build args ([93d0039](https://github.com/teampostharvest/postharvest/commit/93d003943af82d3943f158ba0d31c9cf7d1284e0))
* **docker:** re-resolve nginx upstreams at runtime ([de5a846](https://github.com/teampostharvest/postharvest/commit/de5a846072349de08ea23baca4605e4b38eb0c00))
* **frontend:** clone shared defaults in readScrapeDefaults ([1c3e512](https://github.com/teampostharvest/postharvest/commit/1c3e512b790575330cdd26ba9485a561883176e0))
* **frontend:** resolve eslint error and unused import warnings ([771e791](https://github.com/teampostharvest/postharvest/commit/771e791747e49e7c94931ee2c0a9df06b5324312))


### Features

* **pricing:** add Team tier and refresh plan limits ([3b1b2ce](https://github.com/teampostharvest/postharvest/commit/3b1b2ce473f96850d17d22ca0a5271821099ad49))

## [1.0.1](https://github.com/teampostharvest/postharvest/compare/v1.0.0...v1.0.1) (2026-09-17)


### Bug Fixes

* **scripts:** continue ladder past a non-master hop ([91db989](https://github.com/teampostharvest/postharvest/commit/91db98947f0defc011a3562d6ffebba4ff7b2301))

# 1.0.0 (2026-09-17)


### Bug Fixes

* **auth:** configure Firebase Web SDK frontend build args and fallback defaults ([47357c6](https://github.com/teampostharvest/postharvest/commit/47357c62fc804abb91514547019351ab1f46aafa))
* **cli:** prefer .venv/bin/python over system python in Makefile ([23d80eb](https://github.com/teampostharvest/postharvest/commit/23d80ebdca592ee8698fc0993aefacffcb8c9771))
* **dedup:** truncate timestamp to seconds in content fingerprint ([f361d9b](https://github.com/teampostharvest/postharvest/commit/f361d9bb575f1ef19b7568f1be4e2c4f3ee420f3))
* enforce ops role idempotently on every login ([23247e1](https://github.com/teampostharvest/postharvest/commit/23247e180921c275d5f02d4ef2b74c1bed61090d))
* export downloads were unauthenticated navigations ([e915eb4](https://github.com/teampostharvest/postharvest/commit/e915eb4629eb9bf7c55df337cdea100bbb29c454))
* fixed the unicode problem thing in normalizer.py ([ca27ea1](https://github.com/teampostharvest/postharvest/commit/ca27ea1c87f9a47e0d4903f7c40b413dcde1cc7b))
* keep capture Chromium alive in the hardened container ([caf4f64](https://github.com/teampostharvest/postharvest/commit/caf4f64d000ff97cbb3dbdfdfd60cf02410cc1b7))
* keep the capture dialog centered regardless of page context ([b6a4e2b](https://github.com/teampostharvest/postharvest/commit/b6a4e2b7bd073442336980f17e80930feb1f933c))
* **parser:** tighten date/time parsing and share-count extraction ([7e45405](https://github.com/teampostharvest/postharvest/commit/7e45405dfd0ec3f7aa52f2b7128a173c7f3fd950))
* **progress:** show live posts-based percentage while scraping ([1954ea7](https://github.com/teampostharvest/postharvest/commit/1954ea7139ce5012c7607b480d879f58cf5cba38))
* repair test database setup ([6482baf](https://github.com/teampostharvest/postharvest/commit/6482baf8178908ca0ca4f823c653684f94f6b6df))
* **scraper:** anonymous retry + surface partial feed on login wall ([e9cd873](https://github.com/teampostharvest/postharvest/commit/e9cd87363c69f3e88bd6f769964bcfbaf182ae3f))
* **scraper:** conditional retry, cookie expiry surfacing, and target-scaled scroll patience ([bb2a430](https://github.com/teampostharvest/postharvest/commit/bb2a430184d76d991282dd9e23c79afd19f9a47e))
* **scraper:** extract comments and media types from Comet graphql feed ([db7a005](https://github.com/teampostharvest/postharvest/commit/db7a0053d9279281e2aa8bb91ba6fa23137ebdaa))
* **scraper:** login-wall retry + full final DOM capture + live progress ([ebf7931](https://github.com/teampostharvest/postharvest/commit/ebf7931e208c25d9e3b55b4f547c7d314cc6664e))
* **scraper:** resolve HR-reported data integrity bugs ([d09a80f](https://github.com/teampostharvest/postharvest/commit/d09a80fe68cd680c5118546a3b8a7f1ff332dea1))
* **scraper:** retry cookies twice before falling back anonymous ([99c7bc7](https://github.com/teampostharvest/postharvest/commit/99c7bc74349580fc24434f0ab544ee2cdb41b948))
* surface browser scrape publish errors in CLI normalize step ([99d50b2](https://github.com/teampostharvest/postharvest/commit/99d50b259b60ceb00f27cbfb6078a5a5207dd160))
* **tests:** make capture ws bridge forwarding deterministic ([91820b3](https://github.com/teampostharvest/postharvest/commit/91820b3994a4f97e4282667b65e763bf8465e8a5))


### Features

* **accounts:** add saved Facebook session management API ([025f3a5](https://github.com/teampostharvest/postharvest/commit/025f3a54fe485435e54a44dfb8e49d57298de785))
* always-center and enlarge the capture dialogs ([6f5b80c](https://github.com/teampostharvest/postharvest/commit/6f5b80c0b2c81e39c8abed6a1b71e73038d56501))
* **auth:** implement Firebase authentication and multitenancy architecture ([e654ec3](https://github.com/teampostharvest/postharvest/commit/e654ec360a4fa7cf753710857ca6bb95e82ee6f0))
* **auth:** merge auth feature into testing ([54ad435](https://github.com/teampostharvest/postharvest/commit/54ad43542f704c1b6a756277e94e9839bb5d0148))
* **backend:** add browser-mode scrape fields and paginated job list API ([3995e92](https://github.com/teampostharvest/postharvest/commit/3995e9249ce61741ad08f974f7d7a410307d1a7b))
* **browser:** click timeline tab, snapshot-accumulate feed, capture graphql responses ([f7cc4e6](https://github.com/teampostharvest/postharvest/commit/f7cc4e68ed3f606a9b3e4840ad76fabfab933f61))
* firebase auth, per-user isolation, and tier enforcement ([c4c4433](https://github.com/teampostharvest/postharvest/commit/c4c4433de077f157613ae96fdd820199ddc3a87c))
* **frontend:** add all new pages — investigation, docs, history, settings, accounts ([7bef304](https://github.com/teampostharvest/postharvest/commit/7bef304e9835cb26a07dd96c124ac58a047007b6))
* **frontend:** collapse URL form once a job starts ([afaaf16](https://github.com/teampostharvest/postharvest/commit/afaaf16106d42280a9edb04b08de0f732d5bdb34))
* **frontend:** extract client components to page-content and add SEO ([e31da53](https://github.com/teampostharvest/postharvest/commit/e31da538aefd042ec6d35d4d9677932764577736))
* **frontend:** migrate tailwindcss v3 to v4 ([21e9ae3](https://github.com/teampostharvest/postharvest/commit/21e9ae3de0f998740c82c2caa2c64961c2bcdd9d))
* **frontend:** overhaul scrape form with account dropdown and auto-scroll ([0f0e628](https://github.com/teampostharvest/postharvest/commit/0f0e62856aa98ebfd98f797122cc1581820eb128))
* **frontend:** state-driven animated investigation heading ([753cee7](https://github.com/teampostharvest/postharvest/commit/753cee7f3308d3b18ad5c14bab7ec2b864da59c0))
* **frontend:** upgrade react and react-dom to 19.3.0 ([a11e4a0](https://github.com/teampostharvest/postharvest/commit/a11e4a04c24c70828fe71c9b16b01bd846f16036))
* mailaccess-style split-screen sign-in ([645f44b](https://github.com/teampostharvest/postharvest/commit/645f44be545ff6d9dac4a43d7c3e1961e38160f2))
* make alembic the schema owner for deployments ([dd3dcf1](https://github.com/teampostharvest/postharvest/commit/dd3dcf1aab97fa31f1098314ea7d51b37fc417be))
* mirror cookie jars to Supabase and replace credential login with live session capture ([59962f1](https://github.com/teampostharvest/postharvest/commit/59962f1b7f4f21e75483beacaa694402ab90b60e))
* **parser:** extract posts from embedded Comet graphql feed payloads ([af3df20](https://github.com/teampostharvest/postharvest/commit/af3df205d2d389c9e135df468c47c160c74e5c3d))
* pipe the live Facebook login page into the capture viewer ([86db5df](https://github.com/teampostharvest/postharvest/commit/86db5df0d077304206503572dd2ac0925e291b0b))
* **pricing:** add pricing page and top-nav menu ([df4861e](https://github.com/teampostharvest/postharvest/commit/df4861e737692068226a99e35bcc92dae54a909a))
* **progress:** show percent + ETA + live links box while scraping ([e433549](https://github.com/teampostharvest/postharvest/commit/e433549bc868afa0516d9eb56cf9fc4a42d81609))
* public landing home, top-right sign-in, drop debug badges ([e58a6b8](https://github.com/teampostharvest/postharvest/commit/e58a6b85a0efdc3dcff7b6f406ca2e401cd75e3c))
* serve the session-capture viewer same-origin via CDP proxy ([9d1b1d3](https://github.com/teampostharvest/postharvest/commit/9d1b1d3886965c8fd22c7a6c68d8f2ce59cf4653))
