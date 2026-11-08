# Bird guide integration

Merged November 8, 2026 from the sibling `HACKBU-project-hackathon-NYCAIRR-birdtalker/src/dashboard/`
folder. Only the bird component was imported; the source dashboard's older server, replay controls,
and lighting-rule UI did not replace this project's forecast/dimming functionality.

## Files and behavior

- `src/dashboard/bird-guide-suit.png`: original image, copied unchanged. The source's unused
  `bird-guide.png` is not needed by this component.
- `src/dashboard/bird-guide.css`: widget-scoped styles, mobile bubble positioning, and an animation
  that respects reduced-motion preferences.
- `src/dashboard/bird-guide.js`: isolated script with local helpers, original rotating facts/source
  links, close button, Escape handling, and focus return to the bird toggle.
- `src/dashboard/index.html`: bird markup and assets; the existing summary renderer calls
  `window.BirdGuide?.renderNight(overview)` after a night is selected.
- `src/dashboard/server.py`: explicit allowlist for these three static assets with correct content
  types. Existing API routes remain intact; arbitrary filesystem paths are not served.

The bird opens with a welcome bubble. Click the bird to toggle it, click × or press Escape to
close it, and use **Next fact** to cycle the original three facts and their links. This is a text
speech-bubble guide, as in the supplied source; it does not use audio, chat services, or new API calls.

The nightly explanation was adapted to this project's data contract: forecast mode says migration
forecast, while replay mode says radar. It describes the number of available dimming suggestions
and their average percentage, with the existing planning-estimate qualification. Missing migration
or dimming data remains unavailable; zero remains valid. It does not use the source's obsolete
off/dim/reduce totals or reintroduce the removed orange model banner.

## Verification

```sh
python -m unittest discover -s tests -v
node tests/test_bird_guide.js
```

The Python suite includes asset content/type checks, existing options/page routes, rejection of
unlisted files, and existing forecast, dimming, ranking, and payload regressions. The standalone
Node check requires no extra packages and covers fact cycling, close/Escape, focus return,
forecast/replay summaries, zero/missing values, current dashboard script syntax, integration with
dimming summaries, and graceful operation when the bird script is absent.

Restart the dashboard server to enable the asset routes, then reload the page.
