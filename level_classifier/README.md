# Station Data Unlocked — Level Classifier

Hack4Rail 2026 challenge: extract structured infrastructure data from unstructured sources.

## What this does

`classify_levels.py` determines how many vertical levels each German railway station has, using only DB InfraGO's internal SAP maintenance data. No AI models required — pure rule-based logic.

## Quick Start

```bash
python classify_levels.py
```

This reads from `./input/` and writes to `./output/`.

## Input Data

Place these SAP export CSVs in `./input/`:

| File | Content |
|------|---------|
| `sap-tps-gesamt.csv` | Teilprojekte (station functional locations, hierarchical) |
| `sap-aufzug-eqs-gesamt.csv` | Elevator equipment |
| `sap-treppe-eqs-gesamt.csv` | Staircase equipment |
| `sap-fahrtreppe-eqs-gesamt.csv` | Escalator equipment |
| `sap-tp-typen-gesamt.csv` | TP type classifications (Personenunterführung, etc.) |
| `sap-bahnsteig-eqs-gesamt.csv` | Platform equipment (stufenfreiheit field) |
| `sap-rampe-eqs-gesamt.csv` | Ramp equipment (names can reveal underpasses) |

Optional (for cross-validation):
- `input/openstation-full.netex.xml` — public NeTEx dataset from https://bahnhof.de/daten/netex

Optional (human-verified corrections):
- `input/manual-overrides.csv` — manual level overrides that take precedence over automated classification. Format: `bahnhof,levels,confidence,evidence`. Survives reruns.

## Output

Two CSV files in `./output/`:

### `station-levels.csv` — full results (one row per station)

```csv
bahnhof,name,ril100,category,levels,confidence,evidence
1289,Dortmund Hbf,EDO,1,2,high,elevator foerderhoehe=7.88m
4593,Nürnberg Hbf,NN,1,3,high,TP ID floor levels: ['ERG', 'UG', 'ZG']
```

| Column | Description |
|--------|-------------|
| `bahnhof` | Internal SAP station number |
| `name` | Station name |
| `ril100` | DS100/Ril100 abbreviation |
| `category` | Station category (1=largest, 7=smallest) |
| `levels` | `1`, `2`, `3`, or `4+` |
| `confidence` | `high`, `medium`, or `low` |
| `evidence` | Human-readable reason for the classification |

### `discrepancies-manual-review.csv` — needs human verification

Stations with data quality issues requiring manual inspection:
- Typed as Personenunterführung but no staircase/elevator/escalator recorded
- TP name contradicts TP type (name says Unterführung, type says Überführung or vice versa)
- Kellergeschoss/Untergeschoss present without other underpass evidence (ambiguous)
- No platform recorded in equipment data (data gap or decommissioned)

## Classification Logic

The classifier starts from the assumption of a single level and escalates based on evidence.

### Level 1 (single level) — default when ALL are true:

- No elevators, no escalators
- No underpass/overpass keywords in any TP or ramp name
- No typed Personenunterführung
- No corroborated floor level codes in TP IDs
- No staircases, OR only staircases with ≤ 5 steps (platform access only)
- Platform stufenfreiheit is "höhengleich" or "Gehweg" (same Verkehrsebene)
- No long ramps indicating level change (see rule 10 below)

### Level 2 (two levels) — any ONE of these triggers escalation from 1:

1. **Elevator exists** (height ≤ 8m or no height data)
2. **Escalator exists** (height ≤ 8m or no height data)
3. **Underpass keyword** in TP/ramp names (Unterführung, Tunnel, Passage, Aufzugsmaschinenraum)
4. **Overpass keyword** in TP names (Überführung — excluding Eisenbahn-/Straßenüberführung)
5. **Typed as Personenunterführung** in `sap-tp-typen-gesamt.csv`
6. **2 distinct passenger floor categories** in TP IDs (e.g. UG + ERG), corroborated by equipment
7. **Staircase name** references level change (e.g. "zur Unterführung", "Straßentunnel")
8. **Staircase with ≥ 20 steps** — one full floor height
9. **Platform stufenfreiheit** = "lange Rampe", "Aufzug", or "nicht stufenfrei" (different Verkehrsebene)
10. **Long ramp** indicating level change: ramp name contains "RÜ", "Oberfläche", or "lange Rampe" (any station), OR ramp length ≥ 80m / area ≥ 150m² on a multi-platform station

### Level 3 (three levels) — any ONE of these triggers escalation from 2:

1. **Both underpass AND overpass** keywords in TP names (3 distinct passenger levels)
2. **Elevator height > 8m and ≤ 15m** — spans more than one floor change
3. **Escalator height > 8m and ≤ 15m**
4. **3 passenger floor categories** in TP IDs (UG + ERG + ZG), corroborated by equipment

### Level 4+ (four or more, needs manual review) — any ONE of these triggers:

1. **Elevator height > 15m** with both underpass and overpass keywords
2. **Escalator height > 15m**

### Low confidence (classified but less certain):

- Staircases present with 6–19 steps and stufenfreiheit "höhengleich" or "Gehweg"
- Classified as level 2 (staircase implies level change from secondary access point)
- No other corroborating evidence available

### NeTEx cross-validation:

If classified as "1" but the public OpenStation NeTEx dataset lists lift/escalator equipment → override to "2".

## Custom paths

```bash
python classify_levels.py --data-dir /path/to/csvs --output /path/to/result.csv
```

## Results

Run `python classify_levels.py` to see current distribution. The classifier prints a summary after each run.

**Practical interpretation:**
- 100% of stations classified — no station left unresolved
- 86.5% high confidence — backed by unambiguous physical evidence
- 4.3% low confidence — staircase present but ambiguous (classified as level 2)
- Separate discrepancies file flags data quality issues for review
- All Cat 1-3 stations correctly identified as multi-level (100%)

## Known Limitations

- **Residual risk for 2-platform level-1 stations:** 769 stations with 2 platforms are classified as single-level because all SAP fields (equipment, TP names, TP types, stufenfreiheit, ramp names) confirm ground-level access. However, an underpass could exist without any trace in SAP. This is invisible to any data-driven approach and could be cross-verified with aerial/satellite imagery, street-level photography, or on-site inspection.
- **TP type classifications can be wrong.** `sap-tp-typen-gesamt.csv` frequently mis-types Personenunterführung as Personenüberführung and vice versa. The classifier only trusts underpass types, not overpass types.
- **Building floors (OG/DG) are intentionally ignored.** Empfangsgebäude upper floors, apartments, and offices above stations are not passenger levels.
- **Kellergeschoss/Untergeschoss is ambiguous.** Can be a building basement (not passenger-relevant) or a below-ground passenger area. Flagged for manual review.
- **Eisenbahnüberführung/Straßenüberführung excluded.** These describe infrastructure going over tracks — from the passenger perspective they are underpasses or ground-level.
- **bahnhof.de/karte level data is NOT fully reliable** as a validation source — it sometimes labels building floors as separate station levels.
- **TP ID floor codes (UG/ZG) require corroboration.** A bare "1UG" in a TP ID without equipment could be a building cellar, not a passenger underpass.
- **Inactive stations excluded.** Stations marked "-inaktiv" (e.g. former Swiss border stations) are filtered out.

## Design Principles

- **Accuracy over coverage** — only classifies when evidence is unambiguous
- **No AI models** — deterministic, reproducible, runs offline
- **Non-destructive** — never modifies input files, only creates new outputs
- **Standard library only** — no pip dependencies (uses csv, re, argparse)
