# Live trading setup draft

The **Live Trading** and **Settings** navigation entries open an optional broker and risk-preference form. Broker/exchange, instrument symbols, account currency, maximum order amount and daily loss limit can be left blank and configured later.

Save stores a versioned draft on the current browser under `osiris.liveTradingSetup.v1`. Clear removes it. Drafts do not sync between browsers or users, and a save is not a broker connection or an execution permission. Do not enter API keys, passwords or account credentials in these fields. Secure credential integration and a live-order adapter are future work.

Activation remains disabled regardless of saved values. The backend paper-only policy, risk gates and release inventory are unchanged. Later activation needs a specified broker/account, secure credentials, validated risk controls, order reconciliation and the outstanding release evidence.

Validation: `cd apps/web && npm test && npm run build`. Component tests cover deferred setup, persistence, invalid limits, corrupt/unavailable storage, rejected activation flags, clearing, and both navigation entries.
