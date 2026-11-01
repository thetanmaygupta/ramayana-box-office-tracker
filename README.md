# Ramayana: Rise of a Legend — Box Office Tracker

A personal project tracking advance-booking reports and opening-day box-office prospects for *Ramayana: Rise of a Legend*.

**Live dashboard:** https://thetanmaygupta.github.io/Box-office-tracker/

## What it tracks

- Reported advance-booking snapshots, with source and reporting scope
- India advance-booking figures from an automated check of Sacnilk's public tracker
- Historical opening-day comparisons and illustrative forecast scenarios
- Source links and notes about differences in reporting coverage

## Automatic updates

GitHub Actions runs `scripts/update_data.py` hourly. The script checks Sacnilk's public Ramayana advance-booking tracker, tries to extract the headline India gross, tickets and shows, validates the result, and updates `data.json` only when it can parse a plausible snapshot.

If the page is unavailable or its layout changes, the workflow fails visibly and keeps the last successful figures. This is a best-effort page parser, not an official API, so it may need maintenance if the source changes. Automated extraction does not guarantee that the source itself is accurate or producer-confirmed.

The worldwide and overseas figures in the historical snapshot table are still sourced separately; they are **not** automatically derived from the India tracker. Do not treat different reports as a growth series unless their timestamp, coverage and metric definitions match.

## Data notes

- Advance-booking gross can include blocked seats; the dashboard labels the source's definitions where available.
- Forecasts are estimates, not actual collections or guarantees.
- The dashboard reloads `data.json` in the browser every 15 minutes. New figures become visible after a successful scheduled workflow updates the file and GitHub Pages redeploys.

## Stack

HTML, CSS, JavaScript, JSON, Python and GitHub Actions. The site is hosted on GitHub Pages.

## Disclaimer

This is an independent, non-commercial project created out of an interest in cinema and box-office tracking. It is not affiliated with the film's producers, distributors, exhibitors or trade organisations. Figures are estimates and can differ from official results.

Built by [Tanmay Gupta](https://github.com/thetanmaygupta).
