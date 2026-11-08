# Morphology regression reproduction

The tests were written and run on `fix/long-vowel-morphology` before changing
the analysis implementation. The production service and `main` were not changed.

## Before the fix

On 2026-11-08:

```text
python -m unittest discover -s tests -v
Ran 49 tests in 2.256s
OK

python -m unittest tests.test_morphology_regression -v
Ran 13 tests in 1.091s
FAILED (failures=10)
```

There were nine failing test methods; the lemma-restoration method had two
failing subtests. Four new test methods passed. The existing suite emitted a
Starlette/httpx deprecation warning, without a test failure.

Failures reproduced:

| Area | Observed before implementation |
| --- | --- |
| Derived verbs | `선발하며` and `선발하는` remained their surface lemmas; the plan retained `NNG` rather than restoring the verb `선발하다`/`VV`. |
| Candidate discovery | Neither official `선발하다` candidate was retrieved for `선발하며`. |
| Compounds | `보도부` and `사회혁신융합전공` had no structured component offsets. |
| Proper nouns | An `NNP` tag discarded all component processing even when Kiwi supplied other complete component boundaries. |
| Compound fallback | With a controlled whole-word miss, `보도` and `부` were never queried. |
| Partial coverage | The engine did not inspect the verified short `사회` component. The new assertion also prevents a later implementation from calling a partially verified expression entirely short. |
| Rule/PDF path | A controlled unambiguous `選拔` candidate yielded no mark, so the PDF/rule provenance and manual-adjustment regression could not yet run past its first assertion. |

Already preserved: direct whole-word `보도부` pronunciation, conservative
`사회혁신융합전공` and unknown proper-name handling, `말의` abstention,
`알아` shortening, `끌어` length, and direct short `첫눈`/`밀물`.

Three supplementary test methods were then added for adjective derivation
(`충실하며` / `NNG + XSA`), explicit partial coverage/export metadata, and a
user-selected derived sense surviving reanalysis. At that point the new
morphology implementation was already visible: the adjective test passed;
coverage and selected-sense tests still failed (`Ran 3 tests in 1.023s`, two
failures). The suite now has 16 test methods. The PDF path also checks that
applied rule URLs appear in the exported annotation.

After the initial integration, all 16 morphology test methods passed in 1.067s.
Review then added three further regressions, all reproduced before fixing the
corresponding state-management paths (`Ran 3 tests in 0.883s`, three failures):

- Selecting long `選拔` and then short `先發` changed the occurrence to `none`
  but left the previous circle because a meaning-selection marker had been
  classified as a freehand manual marker.
- Undoing and redoing a selected derived-verb sense lost `rule_inference`
  evidence because restoration only tried exact dictionary pronunciation.
- A memo edit preserved partial component evidence, but restoring its snapshot
  lost the rule evidence type through the same direct-only restoration path.

The morphology regression module now contains 19 test methods.

The three state-management regressions passed after their fixes (0.814s).
A full-suite run then executed 72 tests in 2.868s: all original tests, all 19
morphology regressions, and the three supplementary dictionary tests passed.
One concurrently added export regression failed because the verification
document omitted unselected whole-word/basic-form candidates for ambiguous
`선발하며`. That failure concerned an omitted audit trail; the engine correctly
left the vowel length unresolved.

After that export fix passed, another review test reproduced automatic-source
loss during an unedited restore: `보도부` changed from `dictionary` to `manual`,
and the controlled `선발하며` case changed from `rule` to `manual`. The full
run at that point contained 74 tests (2.614s), with those two failing subtests;
the 19 morphology tests and the new candidate-export test all passed.

## Official evidence and controlled cases

`tests/fixtures/morphology_regression_dictionary_2026-11-08.json` records actual
`stdict.korean.go.kr/api/search.do` exact searches and `view.do` detail responses
captured on 2026-11-08. Records retain the API-derived part of speech, definition,
pronunciations, source URL, and query timestamp. Credential values are omitted.
The older audited fixture supplies the unchanged `말의`, `알아`, and `끌어`
regressions. These are offline replay tests, not API availability tests.

- `보도부` is directly listed as `報道部 [보ː도부]` (143839:423001). Its direct
  reading must take priority over conflicting `보도` homonyms.
- `선발하다` has both `先發 [선발하다]` (184644:193118) and
  `選拔 [선ː발하다]` (185215:425388). The full response must remain unresolved.
  The positive rule test explicitly supplies only the actual `選拔` record to
  isolate length propagation after a meaning has been resolved. It does not
  pretend to establish the meaning automatically.
- The fallback `보도부` test explicitly removes the whole-word response and
  supplies the actual `報道` and `部` records. It verifies a generic component
  rule without inventing dictionary readings or suppressing ambiguity in the
  full-response test.
- `사회` includes short `社會` and long `死灰` homonyms. The full compound test
  prohibits a long prefix mark; a separate controlled short `社會` test leaves
  other components unverified and prohibits an entirely short result.

The small controlled cases verify specific rule behavior. They are not a
measurement of general Korean pronunciation accuracy or semantic coverage.
