# Belarus News Scraper — automation case study

> **Scope:** public educational project; data collection and GitHub Actions, not an AI/LLM agent.

## Problem

Daily collection of technology, finance and general-news headlines is repetitive. A manual copy/paste process creates duplicates, inconsistent timestamps and failed updates when a section is unavailable.

## Solution

A small Python ETL pipeline (extract → transform → load) that retrieves headline cards from three public Onlíner sections, normalizes links, deduplicates records and writes one Excel-compatible UTF-8 BOM CSV file.

```mermaid
flowchart LR
  A[GitHub Actions schedule / manual run] --> B[Python requests]
  B --> C[BeautifulSoup: headline cards]
  C --> D[Normalize + deduplicate]
  D --> E[Atomic CSV write]
  E --> F[Commit only if changed]
```

## Reliability and boundaries

- Up to 30 headlines per section per run; does **not** copy full articles.
- Rate-limited requests; stops on 403/429/CAPTCHA instead of bypassing restrictions.
- A failed section does not necessarily destroy the other sections' collected data.
- A full failure does **not** overwrite the last successful CSV with an empty file.
- CSV is written via a temporary file to avoid partially written results.
- The scraper can break if the source HTML changes; it has no contractual availability guarantee.

## Reproducible demonstration

1. Browse [data/news.csv](../data/news.csv) to see the data format.
2. Review [scraper.py](../scraper.py) and [unit tests](../tests/).
3. Run `python -m unittest discover -s tests -v` locally.
4. Review the latest [GitHub Actions executions](https://github.com/pavelgpt8888-sys/Belarus-News-Scraper/actions).
5. Confirm that unchanged data does not create a new commit.

## Observed evidence

GitHub Actions reported successful scheduled/manual executions on **27, 28 and 29 October 2026** when this case was prepared. Those run statuses are evidence of pipeline execution, **not** proof that each source and every headline was correct. The tests have not been rerun in this authoring session.

## Why this project belongs in a portfolio

It demonstrates API/HTML integration, defensive data processing, scheduled execution, failure handling and reproducible delivery. It does **not** claim autonomous reasoning or LLM integrations. For an AI-agent hackathon, it complements an agent-focused project rather than replacing one.
