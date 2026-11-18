# Evaluation results

Generated 2026-11-18 by `python3 -m peptalk.eval`. 86 questions in `eval/questions.jsonl`.

## Retrieval

Scored on the answerable questions (unanswerable questions have no gold PEPs). hit@k: a gold PEP is in the top k distinct PEPs. MRR: mean reciprocal rank of the first gold PEP (0 when it is not retrieved).

| mode | n | hit@1 | hit@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| bm25 | 83 | 0.867 | 0.988 | 0.918 |
| dense | 83 | 0.904 | 0.964 | 0.938 |
| hybrid | 83 | 0.940 | 1.000 | 0.963 |

### Misses (gold PEP not in the top 5)

**bm25**: 1 miss(es)
- q72: gold PEP 563; top PEPs [484, 681, 585, 695, 604]

**dense**: 3 miss(es)
- q72: gold PEP 563; top PEPs [484, 567, 343, 1, 654]
- q73: gold PEP 572; top PEPs [484, 1, 530, 567, 343]
- q80: gold PEP 3132; top PEPs [530, 343, 3135, 448, 484]

**hybrid**: 0 miss(es)

## Generation

Answers from `peptalk.generate.ask` in `hybrid` mode with k=5, graded by a Claude judge against each reference answer. n = 86.

- citation accuracy (cited PEPs within gold): 0.849
- answer correct: 0.814
- answer partial: 0.093
- judge replies unparsed: 5

| id | kind | cited | citation ok | verdict |
| --- | --- | --- | --- | --- |
| q01 | lookup | PEP 572 | yes | PARTIAL |
| q02 | lookup | PEP 570 | yes | CORRECT |
| q03 | lookup | PEP 465 | yes | CORRECT |
| q04 | lookup | PEP 492, PEP 525 | no | CORRECT |
| q05 | lookup | PEP 343 | yes | unparsed |
| q06 | lookup | PEP 380 | yes | PARTIAL |
| q07 | lookup | PEP 498, PEP 701 | no | CORRECT |
| q08 | lookup | PEP 557 | yes | CORRECT |
| q09 | lookup | PEP 435 | yes | CORRECT |
| q10 | lookup | PEP 8, PEP 526 | no | CORRECT |
| q11 | lookup | PEP 484 | yes | CORRECT |
| q12 | lookup | PEP 604 | yes | CORRECT |
| q13 | lookup | PEP 567 | yes | CORRECT |
| q14 | lookup | PEP 634, PEP 635 | yes | CORRECT |
| q15 | lookup | PEP 589 | yes | CORRECT |
| q16 | lookup | PEP 673 | yes | CORRECT |
| q17 | lookup | PEP 654 | yes | CORRECT |
| q18 | lookup | PEP 617 | yes | CORRECT |
| q19 | lookup | PEP 684, PEP 703 | no | CORRECT |
| q20 | lookup | PEP 703 | yes | unparsed |
| q21 | lookup | PEP 602 | yes | CORRECT |
| q22 | lookup | PEP 20 | yes | CORRECT |
| q23 | lookup | PEP 8 | yes | CORRECT |
| q24 | lookup | PEP 8, PEP 257 | no | CORRECT |
| q25 | lookup | PEP 3135 | yes | CORRECT |
| q26 | lookup | PEP 3119 | yes | CORRECT |
| q27 | lookup | PEP 3333 | yes | CORRECT |
| q28 | lookup | PEP 3156 | yes | CORRECT |
| q29 | lookup | PEP 680 | yes | PARTIAL |
| q30 | lookup | PEP 586 | yes | CORRECT |
| q31 | lookup | PEP 618 | yes | unparsed |
| q32 | lookup | PEP 616 | yes | CORRECT |
| q33 | lookup | PEP 681 | yes | CORRECT |
| q34 | lookup | PEP 597 | yes | CORRECT |
| q35 | lookup | PEP 668 | yes | CORRECT |
| q36 | lookup | PEP 723 | yes | CORRECT |
| q37 | lookup | PEP 563, PEP 3107 | no | CORRECT |
| q38 | lookup | PEP 3129 | yes | CORRECT |
| q39 | lookup | PEP 3132 | yes | CORRECT |
| q40 | lookup | PEP 3120 | yes | PARTIAL |
| q41 | lookup | PEP 3115 | yes | CORRECT |
| q42 | lookup | PEP 318 | yes | CORRECT |
| q43 | lookup | PEP 695 | yes | CORRECT |
| q44 | lookup | PEP 678 | yes | CORRECT |
| q45 | lookup | PEP 698 | yes | CORRECT |
| q46 | lookup | PEP 709 | yes | CORRECT |
| q47 | lookup | PEP 701 | yes | unparsed |
| q48 | lookup | PEP 646 | yes | CORRECT |
| q49 | lookup | PEP 647 | yes | CORRECT |
| q50 | lookup | PEP 675 | yes | CORRECT |
| q51 | lookup | PEP 591 | yes | CORRECT |
| q52 | lookup | PEP 702 | yes | PARTIAL |
| q53 | lookup | PEP 506 | yes | PARTIAL |
| q54 | lookup | PEP 615 | yes | INCORRECT |
| q55 | lookup | PEP 448 | yes | CORRECT |
| q56 | lookup | PEP 585 | yes | CORRECT |
| q57 | lookup | PEP 613 | yes | CORRECT |
| q58 | lookup | PEP 492, PEP 525, PEP 530 | no | CORRECT |
| q59 | lookup | PEP 688 | yes | CORRECT |
| q60 | lookup | PEP 492, PEP 525 | no | CORRECT |
| q61 | lookup | PEP 519 | yes | CORRECT |
| q62 | lookup | PEP 655, PEP 692 | no | CORRECT |
| q63 | lookup | PEP 657 | yes | CORRECT |
| q64 | lookup | PEP 692 | yes | CORRECT |
| q65 | lookup | PEP 544 | yes | unparsed |
| q66 | section | PEP 572 | yes | CORRECT |
| q67 | section | PEP 563 | yes | CORRECT |
| q68 | section | PEP 8, PEP 257 | no | CORRECT |
| q69 | section | PEP 8 | yes | CORRECT |
| q70 | section | PEP 570 | yes | CORRECT |
| q71 | section | - | no | PARTIAL |
| q72 | section | - | no | INCORRECT |
| q73 | section | - | no | INCORRECT |
| q74 | section | PEP 448 | yes | CORRECT |
| q75 | section | PEP 8 | yes | CORRECT |
| q76 | section | PEP 1 | yes | PARTIAL |
| q77 | section | PEP 3156 | yes | CORRECT |
| q78 | section | PEP 3333 | yes | CORRECT |
| q79 | section | PEP 602 | yes | CORRECT |
| q80 | section | PEP 3132 | yes | CORRECT |
| q81 | section | PEP 695 | yes | CORRECT |
| q82 | section | PEP 597 | yes | CORRECT |
| q83 | section | PEP 8 | yes | CORRECT |
| q84 | unanswerable | - | yes | CORRECT |
| q85 | unanswerable | - | yes | CORRECT |
| q86 | unanswerable | - | yes | CORRECT |
