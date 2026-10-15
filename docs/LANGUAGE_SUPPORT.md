# English / Korean support

The main [README](../README.md) is English. Its **한국어** link opens [README.ko.md](../README.ko.md); both editions contain the same installation commands.

The browser platform starts in English. Choose **Language → 한국어** in the header to switch without reloading. The browser remembers the selection when local storage is available. `?lang=en` or `?lang=ko` explicitly selects a language for that URL. Newly generated interactive offline reports include the same selector and do not need a translation service or internet connection.

Translation applies to interface labels, instructions, known user-facing messages, metric captions, and plot labels. Candidate IDs, protein names, sequences, numerical results, original configurations, provenance records, and raw logs remain as recorded. Linked technical documents, original diagnostic notes, terminal output, and browser / operating-system file dialogs may retain their original language. Previously exported HTML and ZIP files remain unchanged; generate a new report to include the language selector.

The shared catalog is `nanocofold/templates/platform-i18n.js`. Presentation call sites explicitly bind translated messages. A language switch updates text nodes and attributes; it does not resubmit computation, reload a structure, reset input controls, or alter saved records. User data must be passed as raw formatting arguments, not used as translation keys.

Regression tests cover the four English example workflows, all four step instructions, Korean switching and persistence, copy-count controls, public-MSA consent, special-character IDs, original logs, and offline structure / observation / numerical-array preservation. Legacy Korean journeys select Korean explicitly and retain their original scientific and interaction assertions.

## Verification on 2026-10-15

The public full suite passed 767 tests and the private platform's related suite passed 63, with browser checks enabled and no failures, errors, or skips. After the final display-binding refinements, the exact newly built and installed wheel passed all 75 related UI, offline-report, privacy, and shared-core checks again. The JavaScript contracts passed 156 public and 75 private checks. README command blocks match across languages, and their local links resolve.

The Python change only adds the translation asset to the existing asset-route allowlists. Prediction, scoring, and selection Python files and the JavaScript report-assessment semantics remain unchanged. A newly rendered native-result preview retained the original embedded data. This verification does not represent new GPU inference or a repeat of the all-profile cold installation. Existing exported reports and delivery ZIPs are preserved.
