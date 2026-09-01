# Delivery notes

## Deletion manifest

**A zip cannot express a deletion.** Extracting over an existing checkout adds
and overwrites; it never removes. Files dropped from the package therefore stay
on disk and keep being picked up by globs. That is not hypothetical: five
superseded experiments survived three extractions this way, one of them ran on
every verification pass, and — because the allocator it builds has no ordering
point to register its leases with — it admitted nothing, passed its oracle on an
empty sample, and exited 0.

Apply these by hand after extracting. Each is a path removed from the package
relative to the previous one.

### This package

    git mv results/e1_safety.json       results/superseded/e1_safety.json
    git mv results/e1_worst_fill.json   results/superseded/e1_worst_fill.json
    git mv results/e2_negative.json     results/superseded/e2_negative.json
    git mv results/e4_conditional.json  results/superseded/e4_conditional.json
    git mv results/e5_adversarial.json  results/superseded/e5_adversarial.json
    git rm WHITEPAPER_SKELETON.md

Extracting this package already places the five JSON files under
`results/superseded/`, so the `git mv` lines are only needed to remove the copies
left at the old paths. Check first:

    git ls-files results/ | grep -v superseded

should list seven `.json` files and `PROVENANCE.md`, nothing else.

### Previous package, applied 2026-09-01

    git rm experiments/e1_safety.py experiments/e1_worst_fill_safety.py \
           experiments/e2_negative.py experiments/e4_conditional.py \
           experiments/e5_adversarial.py

## Verifying a package

Extract into a **new** directory. A new directory reflects what the package
actually contains; extracting over a checkout never does.

    rm -rf ~/Projects/marginstream_check
    mkdir ~/Projects/marginstream_check && cd ~/Projects/marginstream_check
    unzip -q ~/Downloads/marginstream.zip

Then run the enumerated commands in `README.md` — not a glob, which is how a
stale file re-enters the verification set. Once the run is clean, apply the
manifest above in the real checkout and extract there.

## What changed in this package

- `README.md` rewritten. It described the withdrawn price-conditional schedule
  and told the reader to run four files that no longer exist at those paths.
- `WHITEPAPER_SKELETON.md` removed; it carried the same withdrawn model, and
  `README.md` plus `paper/` now cover what it was for.
- `results/` split: seven current files re-recorded in one session with
  `PROVENANCE.md` giving machine, OS, Python, date and commit; five older files
  moved to `results/superseded/` with the commit that produced each.
- `paper/01` §1.7 no longer quotes absolute nanoseconds for E3, only the ratios
  in the recorded file.
- `paper/02` §2.3 separates `G_k` from `G+`, and states the model boundary: every
  correctness experiment uses a seven-point single-factor grid.
- `paper/06` §6.1 corrects the compromised-gateway bound; NFR rows 10 and 11 are
  marked as targets; §4.4's withdrawal posting no longer books two debits in one
  entry; §5.5 is split by tier and no longer claims warm failover meets its
  budget.
- Figures 1 and 2 carry the authority registration and the ordering point's
  binding checks.

No file under `marginstream/`, `tests/` or `experiments/` changed. `results/`
changed by re-recording and archiving, which is the point of the round.

## Outstanding before submission

1. **`paper/09` §9.5 ownership tables are `TODO`.** Four members, nine sections
   and four modules. This cannot be filled in from here and **must not reach the
   PDF as `TODO`**.
2. **Five Mermaid figures have never been rendered.** Four in `paper/diagrams.md`
   and Figure 5 in Appendix B. They are checked structurally only; render all
   five at mermaid.live before exporting (`paper/DIAGRAM_EXPORT.md`).
3. **No PDF has been produced.** The course constraint is 20 pages, not a word
   count. The body is 11,474 words by GNU `wc -w`; BSD `wc` on macOS reads about
   127 higher, which is exactly the number of standalone non-ASCII tokens, so the
   two counts differ by tool and not by content. Neither settles the page count.
4. **Course-pack cross-references unverified.** The running-case citations
   (Part 2 §3, Part 3 §1/§4/§7, Part 5 §1) have not been checked against the
   source material.
