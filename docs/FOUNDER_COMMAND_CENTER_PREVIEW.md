# Founder command center preview

`apps/web/admin.html` is an isolated, static interface preview. It has working
navigation between overview, network, cryptography, modules and configuration
views. Metrics remain unavailable and settings do not change running nodes.
There is no authentication, authorization, telemetry API, private-key integration
or operational control implemented by this page.

Run `npm run dev` from `apps/web` and open `/admin.html`. `npm run build` includes
both the trading dashboard (`dist/index.html`) and this preview
(`dist/admin.html`); `npx vite preview` serves the built
files. The trading dashboard and its existing `/api` development proxy remain
available independently.

Before adding operational controls, implement authenticated APIs, scoped
authorization, input validation, audit history, change previews and rollback.
The static preview and its labels do not establish any of those controls.
