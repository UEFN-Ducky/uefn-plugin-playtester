# Play Tester

Plays the live Fortnite client with Jev vision and world coordinates: sees each frame, knows where the player and the target are, decides moves in about a second, and shows every key, mouse turn and Jev decision live in its tab. Chat agents start in-game play tests with one tool call. Uses the TypeSafe key from Settings → LLMs.

Desktop plugin for [UEFN-Ducky](https://github.com/UEFN-Ducky/UEFN-Ducky) (`playtester`).
Install or update from **Settings → Store** in the app — do not install from a zip by hand.

## Build

```bash
py scripts/build_zip.py --compiled
```

Writes `deploy/playtester-<version>-compiled.ducky-plugin.zip` (scripts/ and deploy/ are not packed). `py scripts/build_zip.py` alone writes the plain source zip.

## Test

```bash
py -m pytest tests -q
```

## Release

```bash
py scripts/release.py --publish --changelog "Improvements and fixes."
```

Commits and pushes this repo, builds the compiled zip with the Store license check baked in, and releases it on uefnducky.org through the Store MCP (`uds_upload_ticket`, `uds_release`). Needs `DUCKYOS_API_KEY` (staff key with `mcp_remote` scopes). Bump `version` in `plugin.json` first.

## License

MIT. Copyright (c) 2026 Mindful Path Company, LLC. See [LICENSE](LICENSE).
