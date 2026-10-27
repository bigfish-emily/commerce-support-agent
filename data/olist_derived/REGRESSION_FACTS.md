# Clean-checkout order facts

`regression_order_facts.json` contains 257 historical public Olist order records
referenced by committed tests and evaluation cases but absent from the original
518-record `agent_records.json` seed. Order facts are copied unchanged from the
derived full index; labels and expected outcomes remain in the evaluation files.

Source: [Olist Brazilian E-Commerce Public Dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).
The source dataset's license and attribution apply to these data separately from
the repository's software license.

Selection is the union of order IDs occurring in `evaluation/fixtures/*.jsonl`,
`evaluation/e2e/*.jsonl` and `tests/test_*.py`, intersected with the full order
index and excluding IDs already in `agent_records.json`. The snapshot was
exported on 2026-10-27. It contains historical facts, not real customer requests
or new evaluation outcomes.

When the full index is absent, the catalog merges these facts into the seed.
This allows demo controls and regression checks to run in a fresh checkout
without requiring a private local data download. Supplying a custom seed via
`OLIST_AGENT_DATASET_PATH` disables this supplemental merge.
