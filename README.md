[README.md](https://github.com/user-attachments/files/28722394/README.md)
# Serif Health Data Scientist Take Home Assessment

**Goal:** Match TIC and HPT files.

## Overview of Approach

The two files (TIC and HPT) are combined into one table by matching on the five variables listed below (see table), using a mix of exact and fuzzy logic per variable and scoring each match. In this approach, there is no single exact join. A pipeline was created to handle various types of logic. The pipeline pre-filters the HPT file to eligible rows (PPO plans, with the local code type dropped), joining on code type plus the billing code extracted from `raw_code`, applies the setting rule and a fuzzy payer-name threshold as gates, and then keeps every row, matched or otherwise, with the rate delta and a confidence score on matches.

## How to Run the Code

Requirements: Python 3.9 or later, with `pandas` and `rapidfuzz`.

```bash
python SerifHealthMatching.py
```

Set the input paths and the column maps at the top of the script, then run the command above. The script reads the two files, performs the match, and writes the unified output to `serif_health_output.csv`.

## Input Schemas and Unified Output

The payer (TIC) extract provides negotiated PPO rates, with the fields `payer`, `network_name`, `code_type`, `code`, `cms_baseline_schedule`, and `rate`. The hospital (HPT) extract provides hospital-published rates, with `payer_name`, `plan_name`, `code_type`, `raw_code`, `setting`, and gross charge.

The unified output has one row per matched pair plus one row for each unmatched record from either file. The script adds `status` (matched, tic_unmatched, or hpt_unmatched), `confidence`, `payer_score`, `code_score`, `rate_tic`, `rate_hpt`, `rate_diff` (negotiated minus charge), `rate_ratio`, and `hpt_exclusion` (the reason an HPT row was filtered). Every original column is carried through, prefixed `tic_` or `hpt_`. At the row level this shows whether a data point came from the payer file, the hospital file, or both, and the delta where both are present.

## Created Rules

- `payer` in TIC gets matched to `payer_name` in HPT. This needs to be fuzzy matching as there are several versions for United Healthcare in the HPT file.
- `network_name` in TIC gets "matched" to `plan_name` in HPT. In reality, we just need to pull out the ones in the HPT file that have "PPO" in the `plan_name`.
- `code_type` in TIC will match to `code_type` in HPT. This should drop the "local" code_type in the HPT file.
- `code` in TIC gets matched to `raw_code` in HPT. Kind of fuzzy matching because the HPT file has some text in the `raw_code` column that influences this match.
- `cms_baseline_schedule` in TIC gets matched to `setting` in HPT with the following rules:
  - Setting labeled as "both" in HPT, match to `_facility`, `_nonfacility`, `ipps`, and `opps` in TIC (partial string match).
  - Setting labeled as "inpatient" in HPT, match to `ipps` in TIC.
  - Setting labeled as "outpatient" in HPT, match to `_facility` and `opps` in TIC (partial string match).

So, the TIC/HPT files are matched on:

| Variable | TIC | HPT |
|---|---|---|
| Payer | `payer` | `payer_name` |
| Plan / network | `network_name` | `plan_name` |
| Code type | `code_type` | `code_type` |
| Billing code | `code` | `raw_code` |
| Setting / schedule | `cms_baseline_schedule` | `setting` |

These columns were chosen as they most materially affect the rate. For example, using `plan_name` to get to the PPO plans (the only ones included in the TIC file) is necessary as PPO plans allow for different amounts than an HMO (or a different one) plan.

All new columns are the first columns in the new file.

> **Note:** This is a different setup than I normally work. I've always worked with very limited disk space, so I'm used to decreasing the size of the tables first (aiming for just the rows I need) before matching.

## Matching Approach and Confidence Score

Each variable has its own comparison: exact on `code_type`, a conditional substring rule on `setting`, the billing code extracted from `raw_code` and then matched, and fuzzy matching on payer using RapidFuzz (WRatio) after an alias map canonicalizes variants such as UHC and United to United Healthcare. The PPO filter, code type, setting rule, and code match are hard gates that decide whether a pair qualifies; the fuzzy payer match also has a minimum threshold.

The confidence score summarizes the soft dimensions of a qualifying pair as `(2 * payer_score + code_score) / 3`, where `payer_score` is the fuzzy payer similarity (1.0 when aliases resolve to the same payer) and `code_score` is 1.0 for an exact extracted-code match. Known limitations: payer acronyms that are not in the alias map will not match and need to be added (see below for future directions); codes glued (e.g., no spaces, dashes) to surrounding text need a tuned extraction pattern (not an issue in this file, but could be for future files). DRG codes are bundled and one-to-many, so they do not tie out cleanly. At national volume, blocking on code type plus code is what keeps the comparison tractable, and the hand-set weights would be replaced by a learned probabilistic score.

## Plan Type: Matching Key or Confidence Score

Plan type is in the matching key: the HPT file is filtered to PPO plans because the TIC extract is PPO-only, so a non-PPO hospital row cannot have a counterpart in this data. That is clean and precise for this extract.

As the data broadens to more plan types in a national dataset scaleup, keeping plan type as a hard key risks dropping legitimate near-matches, for example a plan labeled inconsistently or an EPO that negotiates like a PPO. The alternative is to move plan type into the confidence score as a feature: still compare across plan types, but down-weight pairs whose plan types disagree. A key gives higher precision but can silently exclude; a scored feature gives higher recall and a graded signal but depends on a reliable plan-type variable, which means parsing `plan_name` into a normalized type first. For this submission plan type is a key given the PPO-only extract, and I would move it into the score at scale.

## Scaling to National Dataset and With Additional Time

There are several other steps that I would take for a scaleup to a national file and/or with more time:

- As the file grows to the national scale, I would expect the fill rates to decrease as there is significant missing or hard-to-match data. It's possible that the match rate could be improved, but there are significant differences between the two files that don't lend themselves to a complete match.
- I would add a drift detection and correction algorithm. As more data are added and new issues and fuzziness occur, the original algorithms are going to drift and be less effective. Corrections would need to occur to keep the algorithms working properly. Along those lines too would be a manual QA process to make sure the algorithms and drift detections/corrections are continuing to work properly.
- The algorithms would need to be updated for other issues that arise such as very fuzzy matching, other insurances, potentially start and stop dates for insurances, etc.
- Other functions could be added to handle different text issues as they arise, particularly in the `code_type` variable.
- Normalization pipelines would be beneficial. Normalizing the insurance names to true names rather than aliases/abbreviations would potentially speed up the pipeline. Along this line too would be creating a column that would pull apart `plan_name` in the HPT file to denote the type of insurance (e.g., PPO, EPO, HMO).
- Expansion of the alias mapping. I've included the first steps of an alias map in the script, but it could be built out further.
- For scaling to a national dataset, the script could be optimized/updated to work in PySpark or some sort of distributed computing environment.
- The matching scheme would also need to be tuned for a national dataset, potentially adding different or additional weights to account for different code types and the potential reuse of codes.
- For testing and releasing to production: I would start with a large file (not the national file) to check for any issues that could arise, then fix those before a scaleup to the national file. I would include validation checks, regression tests on known matches to determine places that the algorithm could be improved and implement those changes, and a staged rollout in case there are any issues that arise from the implementation of the code.

## Potential Reasons for Rate Differences

There are quite a few reasons that rates could be different between the two files, including:

- The time it takes to complete the procedure.
- Inclusion of facility fees in the rates, in particular in the HPT file.
- Whether anesthesia was included in the procedure rate or not. The anesthesiologist may not be on staff at the hospital, but rather contracted out.
  - Also included with anesthesia would be the type used – general vs twilight.
- Whether pathology was included in the procedure rate or not. The amount of pathology needed would also affect the rate – there should be a difference in charges if one biopsy is taken and read versus a couple dozen.
- The difference between in-network and out-of-network providers.
- Possible complexities of the procedures, e.g., depth of biopsy, bleeding, other complications.
- Any CPT modifiers used.
- With the rate differences, I would want to understand what goes into each rate calculation from each location. It could be that the hospitals/practices include different items in their calculation and therefore the rates are not totally comparable. For use later, I would set criteria to determine which rate differences are okay and which rate differences require further review. It could be an issue in the match, the file, or something else. Before using it in any modeling, I would want to make sure that the numbers and the match are correct.

## DRG vs. CPT/HCPCS Codes

DRG codes are inpatient-only codes. CPT/HCPCS codes can be used inpatient or outpatient. Each coding scheme also serves different purposes. CPT/HCPCS focus on the procedure itself, where DRG codes relate to conditions causing the entire hospital stay. They would fundamentally cover different parts of a hospital stay.

**DRG 872:** This code is the inpatient hospital code for sepsis/septicemia. DRG rates differ from CPT/HCPCS codes as it covers an inpatient hospital stay (e.g., the entire stay) where CPT/HCPCS codes cover one piece. DRG rates are calculated for the expected usage of that condition while the patient is in the hospital. As it's a more set rate, the hospital could profit or lose money depending on the patient. For CPT codes, there is a range of potential prices, including the use of modifiers that can increase the price of a procedure, where the hospital isn't necessarily likely to make or lose money.

For the specific example in the instructions (DRG 872 at Montefiore): the Montefiore Aetna PPO rate doesn't appear in the Aetna TIC. It may not appear because of how the PPO rate was negotiated – that the PPO is at a per-diem rate rather than the flat fee of a DRG code. Montefiore may also appear under a different identifier (TIN/NPI) in the Aetna file than the one it's listed under in the hospital file, so the rows don't link. Within the output file, this shows up as unmatched. For future use, any DRG codes that do not have equivalents would need to be handled with comparisons of the rate basis to understand which basis the DRG rate belongs to (e.g., per diem, case rate).

**CPT 43239:** For selected reasons why the prices could vary, see the section titled "Potential Reasons for Rate Differences." Other reasons the price could vary include: different places of service or different providers at the same facility (each with its own cost), each PPO negotiating differently, different ways the rate is actually calculated (e.g., negotiated fee-for-service), and the inclusion of modifiers. Once you condition on the same setting, plan, and provider, the additional rows align with their TIC counterparts rather than being duplicates. 43239 is the case that does match (6438 appears in both files), whereas DRG 872 is the case that does not.

## Assumptions and Decisions

The key assumptions and decisions behind this approach, with the reasoning for each:

- **PPO-only files:** The TIC contains only PPO rates, so the HPT file is filtered to PPO plans and a non-PPO hospital row is treated as having no possible counterpart.
- **Code is treated as an exact key:** The TIC code must exactly equal a token extracted from `raw_code`; because billing codes are standardized, this is safer than fuzzy code matching and avoids false links between similar codes.
- **The setting rule is authoritative:** The both, inpatient, and outpatient mapping to the `cms_baseline_schedule` substrings is applied as provided, with no fuzzy interpretation (see rules above).
- **Payer is the only fuzzy field:** Payer names vary in spelling and abbreviation, so payer uses fuzzy matching with an alias map; every other field is exact or rule-based, which keeps precision high. Payer matching depends on the alias map for abbreviations such as UHC and United; a payer not yet in the map will not match until it is added.
- **Local code types are dropped:** HPT rows with a local code_type are assumed to be hospital-internal and not comparable across files.
- **Rate comparison fields:** `rate_diff` and `rate_ratio` compare the TIC negotiated rate against the HPT gross charge; if a different HPT price column is the intended basis, it should be swapped in.
- **All rows are kept:** The output is a full view (matched, tic_unmatched, hpt_unmatched) rather than an inner join, so a no-match is visible rather than silently dropped, and a TIC row that matches several HPT rows keeps all of those matches.
- **Confidence weights are hand-set:** The score weights payer similarity at twice the code score; this is a reasonable starting point rather than a learned or validated value.

With more time, I would:

- Confirm the correct HPT price column for the rate comparison.
- Replace the hand-set confidence weights with a learned, probabilistic score, validated against known matches such as CPT 43239.
- Handle DRG bundling and one-to-many matching explicitly rather than treating all code types the same.
- Expand the alias map and parse `plan_name` into a normalized plan type (also noted under Scaling).
