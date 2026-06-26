"""
Station Level Classifier — Conservative, rule-based (no AI models).

Classifies German railway stations by number of vertical levels.
Only classifies when evidence is unambiguous; flags uncertain cases for manual review.

Levels:
    1       = single level (ground access to platform, no vertical transport)
    2       = two levels (platform + underpass/overpass)
    3       = three levels (e.g. underpass + platform + overpass)
    4+      = four or more levels (needs manual review)

Usage:
    python classify_levels.py [--data-dir DIR] [--netex FILE] [--output FILE]
"""
import argparse
import csv
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

# === PATTERNS ===

# TP names proving multi-level (underpass side)
# Excludes Straßenunterführung (road goes under tracks, passengers are above)
# Excludes Kellergeschoss/Untergeschoss (ambiguous — could be building basement or passenger level)
UNDERPASS_PATTERN = re.compile(
    r'((?<!stra[ßs]en)unterf[üu]hrung|(?<!stra[ßs]en)unterf\b'
    r'|\btunnel\b|personentunnel|fu[ßs]g[äa]ngertunnel'
    r'|\bpassage\b|tiefgeschoss'
    r'|\bPU\b|\bP\.?U\.?\b|treppenabgang'
    r'|aufzugsmaschinenraum)',  # elevator machine room = elevator exists = level change
    re.IGNORECASE
)

# TP names proving multi-level (overpass side)
# Excludes Eisenbahnüberführung (railway over road) and Straßenüberführung (road over tracks)
# — both describe infrastructure going over, not a pedestrian overpass
OVERPASS_PATTERN = re.compile(
    r'((?<!eisenbahn)(?<!stra[ßs]en)[üu]berf[üu]hrung'
    r'|reisenden[üu]berweg.*stufen)',
    re.IGNORECASE
)

# Staircase names proving level change
STAIRCASE_MULTILEVEL_PATTERN = re.compile(
    r'(zur?\s*(Unterf|Tunnel|EÜ|PU|Passage)'
    r'|zum?\s*(Unterf|Tunnel|EÜ|PU|Passage)'
    r'|[Üü]berf[üu]hrung|Br[üu]cke|Steg'
    r'|Stra[ßs]entunnel'
    r'|Treppenaufgang|Treppenabgang)',
    re.IGNORECASE
)

# Floor level codes in TP IDs: "-1UG-", "-ERG-", "-1ZG-" etc.
# Only UG (underground) and ZG (Zwischengeschoss/mezzanine) are reliable passenger levels.
# OG/DG are mostly building floors (offices) and not counted.
TP_ID_FLOOR_PATTERN = re.compile(r'-(\d*(?:UG|ZG)|ERG)(?:-|$)', re.IGNORECASE)

# Ramp names indicating platforms are at a different level than the surrounding terrain.
# "Oberfläche" = surface access (platforms are below), "RÜ" = Reisenden­überweg via underpass.
RAMP_LEVEL_CHANGE_PATTERN = re.compile(
    r'(Oberfl[äa]che|\bRÜ\b|lange\s+Rampe)',
    re.IGNORECASE
)

# Thresholds for ramp dimensions that imply a genuine level change.
# Per erfassung-barrierefreiheit.pdf: "Lange Rampe" is officially defined as >50m length
# with >3% continuous slope, connecting "verschiedene Verkehrsebenen" (different traffic levels)
# or platforms to Personenunter-/überführungen. At 3% over 50m = 1.5m height; at 6% = 3m.
# We use 80m as a conservative threshold since we cannot verify slope from the CSV data.
RAMP_LENGTH_THRESHOLD = 80      # meters (laenge_ib)
RAMP_AREA_THRESHOLD = 150       # m² (grundflaeche) — proxy when length is missing


# === HELPERS ===

def parse_decimal(value):
    if not value:
        return None
    try:
        return float(value.replace(',', '.'))
    except (ValueError, AttributeError):
        return None


def parse_integer(value):
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, AttributeError):
        return None


def load_csv(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def group_by_station(rows):
    groups = {}
    for row in rows:
        station_id = row.get('bahnhof', '').strip()
        if station_id:
            groups.setdefault(station_id, []).append(row)
    return groups


def max_height(equipment_list, field='foerderhoehe'):
    best = 0
    for eq in equipment_list:
        height = parse_decimal(eq.get(field, ''))
        if height and height > best:
            best = height
    return best if best > 0 else None


def _has_multilevel_stufenfreiheit(platform_stufenfreiheit):
    """Check if any platform indicates a different Verkehrsebene."""
    if not platform_stufenfreiheit:
        return None
    for sf in platform_stufenfreiheit:
        if sf.lower() in ('lange rampe', 'aufzug', 'nicht stufenfrei'):
            return sf
    return None


def _detect_long_ramp(ramps, platform_count):
    """
    Detect ramps that indicate a genuine level change based on dimensions or name.

    Returns evidence string if found, None otherwise.

    Criteria:
    - Ramp name contains "Oberfläche", "RÜ", or "lange Rampe" (direct evidence)
    - Ramp laenge_ib >= 80m on a multi-platform station
    - Ramp grundflaeche >= 150m² on a multi-platform station (proxy when length missing)
    """
    if not ramps:
        return None

    for ramp in ramps:
        if ramp.get('is_inak') == '1' or ramp.get('is_snno') == '1':
            continue

        name = ramp.get('name', '')
        laenge = parse_decimal(ramp.get('laenge_ib', ''))
        grundflaeche = parse_decimal(ramp.get('grundflaeche', ''))

        # Name-based: direct level-change evidence regardless of dimensions
        if RAMP_LEVEL_CHANGE_PATTERN.search(name):
            return f"ramp '{name}' (name indicates level change)"

        # Dimension-based: only on multi-platform stations (single-platform ramps are
        # often just long flat access paths from parking lots)
        if platform_count >= 2:
            if laenge and laenge >= RAMP_LENGTH_THRESHOLD:
                return f"ramp '{name}' length={laenge:.0f}m (≥{RAMP_LENGTH_THRESHOLD}m)"
            if grundflaeche and grundflaeche >= RAMP_AREA_THRESHOLD:
                return f"ramp '{name}' area={grundflaeche:.0f}m² (≥{RAMP_AREA_THRESHOLD}m²)"

    return None


# === CLASSIFICATION ===

def classify_station(elevators, escalators, staircases, tp_names, tp_ids,
                     has_typed_underpass=False, platform_stufenfreiheit=None,
                     platform_count=0, ramps=None):
    """
    Classify a station's vertical level count.

    Returns dict: {levels, confidence, evidence}
    levels: "1" | "2" | "3" | "4+"
    confidence: "high" | "medium" | "low"
    """
    evidence = []

    # --- Gather structural evidence ---
    has_underpass = has_typed_underpass or any(UNDERPASS_PATTERN.search(tp_name) for tp_name in tp_names)
    has_overpass = any(OVERPASS_PATTERN.search(tp_name) for tp_name in tp_names)
    has_elevator = bool(elevators)
    has_escalator = bool(escalators)
    elev_height = max_height(elevators)
    esc_height = max_height(escalators, 'foerderhoehe')

    # Floor levels from TP IDs (UG=underground, ERG=ground, ZG=mezzanine)
    floor_categories = set()  # 'UG', 'ERG', 'ZG'
    for tp_id in tp_ids:
        for match in TP_ID_FLOOR_PATTERN.finditer(tp_id):
            code = match.group(1).upper()
            if 'UG' in code:
                floor_categories.add('UG')
            elif 'ZG' in code:
                floor_categories.add('ZG')
            elif code == 'ERG':
                floor_categories.add('ERG')

    # --- 3 levels: confident cases / 4+: flag for review ---

    # Both underpass AND overpass in TP names
    if has_underpass and has_overpass:
        if elev_height and elev_height > 15:
            evidence.append(f"underpass + overpass + elevator {elev_height}m (>15m)")
            return _r('4+', 'medium', evidence)
        evidence.append("both underpass and overpass in TP names")
        return _r('3', 'high', evidence)

    # Elevator height > 8m = spans more than one floor
    if elev_height and elev_height > 8:
        if elev_height > 15:
            evidence.append(f"elevator foerderhoehe={elev_height}m (>15m)")
            return _r('4+', 'medium', evidence)
        evidence.append(f"elevator foerderhoehe={elev_height}m (>8m → 3 levels)")
        return _r('3', 'high', evidence)

    # Escalator height > 8m
    if esc_height and esc_height > 8:
        if esc_height > 15:
            evidence.append(f"escalator foerderhoehe={esc_height}m (>15m)")
            return _r('4+', 'medium', evidence)
        evidence.append(f"escalator foerderhoehe={esc_height}m (>8m → 3 levels)")
        return _r('3', 'high', evidence)

    # 3 passenger floor categories (UG + ERG + ZG) — only trust when corroborated by equipment
    has_any_equipment = has_elevator or has_escalator or bool(staircases)
    if len(floor_categories) >= 3 and (has_any_equipment or has_underpass or has_overpass):
        evidence.append(f"TP ID floor levels: {sorted(floor_categories)}")
        return _r('3', 'high', evidence)

    # --- 2 levels: confident cases ---

    # Elevator with height ≤ 8m (one floor change)
    if elev_height and elev_height <= 8:
        evidence.append(f"elevator foerderhoehe={elev_height}m")
        return _r('2', 'high', evidence)

    # Elevator present (no height data)
    if has_elevator:
        evidence.append(f"{len(elevators)} elevator(s)")
        return _r('2', 'high', evidence)

    # Escalator with height ≤ 8m
    if esc_height and esc_height <= 8:
        evidence.append(f"escalator foerderhoehe={esc_height}m")
        return _r('2', 'high', evidence)

    # Escalator present (no height data)
    if has_escalator:
        evidence.append(f"{len(escalators)} escalator(s)")
        return _r('2', 'high', evidence)

    # Underpass OR overpass keyword in TP names
    if has_underpass:
        match = next((tp_name for tp_name in tp_names if UNDERPASS_PATTERN.search(tp_name)), None)
        evidence.append(f"TP: '{match}'" if match else "typed as Personenunterführung")
        return _r('2', 'high', evidence)

    if has_overpass:
        match = next((tp_name for tp_name in tp_names if OVERPASS_PATTERN.search(tp_name)), None)
        evidence.append(f"TP: '{match}'")
        return _r('2', 'high', evidence)

    # 2 passenger floor categories — only trust when corroborated by equipment
    if len(floor_categories) >= 2 and (has_any_equipment or has_underpass or has_overpass):
        evidence.append(f"TP ID floor levels: {sorted(floor_categories)}")
        return _r('2', 'medium', evidence)

    # --- Staircase analysis (resolve ambiguous cases) ---

    if staircases:
        # Staircase name with explicit multi-level destination
        for stair in staircases:
            name = stair.get('name', '')
            if STAIRCASE_MULTILEVEL_PATTERN.search(name):
                evidence.append(f"staircase: '{name}'")
                return _r('2', 'medium', evidence)

        # Step count: ≥20 steps ≈ one full floor
        max_steps = 0
        for stair in staircases:
            steps = parse_integer(stair.get('anzahl_stufen', ''))
            if steps and steps > max_steps:
                max_steps = steps

        if max_steps >= 20:
            evidence.append(f"staircase {max_steps} steps (≥20 → full floor)")
            return _r('2', 'high', evidence)

        # Use stufenfreiheit to resolve: "lange Rampe"/"Aufzug"/"nicht stufenfrei" = real level change
        sf_signal = _has_multilevel_stufenfreiheit(platform_stufenfreiheit)
        if sf_signal:
            confidence = 'high' if platform_count <= 1 else 'medium'
            evidence.append(f"stufenfreiheit='{sf_signal}' (different Verkehrsebene)")
            return _r('2', confidence, evidence)

        # höhengleich/Gehweg + staircase ≤5 steps: platform access only → single level
        if max_steps > 0 and max_steps <= 5:
            evidence.append(f"staircase(s) max {max_steps} steps (platform access)")
            return _r('1', 'medium', evidence)

        # 6-19 steps with höhengleich: staircase proves level change from another access point
        evidence.append(f"{len(staircases)} staircase(s), max {max_steps} steps")
        return _r('2', 'low', evidence)

    # --- Single level (no equipment, no keywords) ---

    # But check stufenfreiheit: if platform is at a different Verkehrsebene, it's level 2
    sf_signal = _has_multilevel_stufenfreiheit(platform_stufenfreiheit)
    if sf_signal:
        confidence = 'high' if platform_count <= 1 else 'medium'
        evidence.append(f"stufenfreiheit='{sf_signal}' (different Verkehrsebene)")
        return _r('2', confidence, evidence)

    # Long ramp detection: ramps with extreme dimensions or names indicating level change
    long_ramp_evidence = _detect_long_ramp(ramps or [], platform_count)
    if long_ramp_evidence:
        evidence.append(long_ramp_evidence)
        return _r('2', 'medium', evidence)

    evidence.append("no vertical transport; no underpass/overpass in TP names")
    return _r('1', 'high', evidence)


def _r(levels, confidence, evidence):
    return {'levels': levels, 'confidence': confidence, 'evidence': '; '.join(evidence)}


# === NeTEx cross-validation ===

def scan_netex_multilevel(netex_path):
    """Return set of Ril100 codes for stations with lift/escalator in NeTEx."""
    with open(netex_path, 'r', encoding='utf-8') as f:
        content = f.read()

    ril_pattern = re.compile(r'<Key>RIL</Key>\s*<Value>([^<]+)</Value>')
    multilevel = set()

    for match in re.finditer(r'<StopPlace [^>]*>.*?</StopPlace>', content, re.DOTALL):
        block = match.group()
        if '<LiftEquipment ' in block or '<EscalatorEquipment ' in block:
            for ril in ril_pattern.finditer(block):
                multilevel.add(ril.group(1).strip())

    return multilevel


# === MAIN ===

def main():
    parser = argparse.ArgumentParser(description='Classify station levels from SAP data')
    parser.add_argument('--data-dir', default=os.path.join('.', 'input'))
    parser.add_argument('--netex', default=os.path.join('.', 'input', 'openstation-full.netex.xml'))
    parser.add_argument('--output', default=os.path.join('.', 'output', 'station-levels.csv'))
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    print("Loading data...")
    all_tps = load_csv(os.path.join(args.data_dir, 'sap-tps-gesamt.csv'))
    all_elevators = load_csv(os.path.join(args.data_dir, 'sap-aufzug-eqs-gesamt.csv'))
    all_staircases = load_csv(os.path.join(args.data_dir, 'sap-treppe-eqs-gesamt.csv'))
    all_escalators = load_csv(os.path.join(args.data_dir, 'sap-fahrtreppe-eqs-gesamt.csv'))

    # TP types: explicit classification of station areas (underpass only — overpass type is unreliable)
    tp_types_path = os.path.join(args.data_dir, 'sap-tp-typen-gesamt.csv')
    station_has_underpass = set()
    tp_type_details = {}
    if os.path.exists(tp_types_path):
        for row in load_csv(tp_types_path):
            bf = row.get('bahnhof', '').strip()
            typ = row.get('oeffentlicher_ort_typ', '').strip()
            if typ == 'Personenunterführung':
                station_has_underpass.add(bf)
            tp_type_details.setdefault(bf, []).append({
                'tp_id': row.get('id', ''),
                'tp_name': row.get('name', ''),
                'tp_type': typ,
            })
        print(f"  TP types: {len(station_has_underpass)} stations with underpass")

    # NeTEx cross-validation
    netex_multilevel = set()
    if os.path.exists(args.netex):
        print("Loading NeTEx cross-validation...")
        netex_multilevel = scan_netex_multilevel(args.netex)
        print(f"  {len(netex_multilevel)} stations with lift/escalator in NeTEx")

    # Station registry
    station_registry = {}
    for tp in all_tps:
        bf = tp.get('bahnhof', '').strip()
        if bf and tp.get('hierarchie_tiefe') == '1':
            name = tp.get('name', '')
            if '-inaktiv' in name.lower():
                continue  # Skip inactive stations
            station_registry[bf] = {
                'name': name,
                'ril100': tp.get('ril100', ''),
                'category': tp.get('bf_kategorie', ''),
            }

    # TP names and IDs per station (exclude inactive: SNNO = not in use, INAK/inaktiv = inactive)
    tp_names_by_station = {}
    tp_ids_by_station = {}
    for tp in all_tps:
        bf = tp.get('bahnhof', '').strip()
        name = tp.get('name', '')
        tp_id = tp.get('id', '')
        if 'SNNO' in name or 'INAK' in name or 'inaktiv' in name.lower():
            continue
        if bf and name:
            tp_names_by_station.setdefault(bf, []).append(name)
        if bf and tp_id:
            tp_ids_by_station.setdefault(bf, []).append(tp_id)

    # Group equipment
    elevators_by_station = group_by_station(all_elevators)
    escalators_by_station = group_by_station(all_escalators)
    staircases_by_station = group_by_station(all_staircases)

    # Ramp names (can reveal underpasses: "Rampe zur Unterführung")
    all_ramps = load_csv(os.path.join(args.data_dir, 'sap-rampe-eqs-gesamt.csv'))
    ramps_by_station = group_by_station(all_ramps)

    # Platform stufenfreiheit and count per station
    all_platforms = load_csv(os.path.join(args.data_dir, 'sap-bahnsteig-eqs-gesamt.csv'))
    stufenfreiheit_by_station = {}
    platform_count_by_station = {}
    for plat in all_platforms:
        bf = plat.get('bahnhof', '').strip()
        sf = plat.get('stufenfreiheit', '').strip()
        if bf:
            platform_count_by_station[bf] = platform_count_by_station.get(bf, 0) + 1
            if sf:
                stufenfreiheit_by_station.setdefault(bf, []).append(sf)

    # Classify
    print(f"Classifying {len(station_registry)} stations...")
    results = []

    # Manual overrides: human-verified corrections that survive reruns
    overrides = {}
    overrides_path = os.path.join(args.data_dir, 'manual-overrides.csv')
    if os.path.exists(overrides_path):
        for row in load_csv(overrides_path):
            bf = row.get('bahnhof', '').strip()
            if bf:
                overrides[bf] = row
        print(f"  Manual overrides: {len(overrides)} stations")

    for bf in sorted(station_registry, key=lambda x: int(x) if x.isdigit() else 0):
        info = station_registry[bf]

        # Manual override takes precedence over all automated classification
        if bf in overrides:
            ov = overrides[bf]
            classification = _r(
                ov.get('levels', '2'),
                ov.get('confidence', 'high'),
                [f"manual: {ov.get('evidence', 'human-verified override')}"],
            )
        else:
            classification = classify_station(
                elevators=elevators_by_station.get(bf, []),
                escalators=escalators_by_station.get(bf, []),
                staircases=staircases_by_station.get(bf, []),
                tp_names=tp_names_by_station.get(bf, [])
                    + [r.get('name', '') for r in ramps_by_station.get(bf, [])],
                tp_ids=tp_ids_by_station.get(bf, []),
                has_typed_underpass=(bf in station_has_underpass),
                platform_stufenfreiheit=stufenfreiheit_by_station.get(bf, []),
                platform_count=platform_count_by_station.get(bf, 0),
                ramps=ramps_by_station.get(bf, []),
            )

            # NeTEx override: if we say "1" but NeTEx has lift/escalator
            if classification['levels'] == '1' and info['ril100'] in netex_multilevel:
                classification = _r('2', 'high', ['NeTEx has lift/escalator for this station'])

        results.append({
            'bahnhof': bf,
            'name': info['name'],
            'ril100': info['ril100'],
            'category': info['category'],
            'levels': classification['levels'],
            'confidence': classification['confidence'],
            'evidence': classification['evidence'],
        })

    # Write full results
    with open(args.output, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'bahnhof', 'name', 'ril100', 'category', 'levels', 'confidence', 'evidence'
        ])
        writer.writeheader()
        writer.writerows(results)

    # Write discrepancies for manual review
    output_dir = os.path.dirname(args.output)
    discrepancies_path = os.path.join(output_dir, 'discrepancies-manual-review.csv')
    discrepancies = []

    for result in results:
        bf = result['bahnhof']
        has_typed = (bf in station_has_underpass)
        has_equipment = bool(elevators_by_station.get(bf)) or bool(escalators_by_station.get(bf)) or bool(staircases_by_station.get(bf))
        if has_typed and not has_equipment:
            details = tp_type_details.get(bf, [])
            for detail in details:
                if detail['tp_type'] in ('Personenunterführung', 'Personenüberführung', 'Zugangsbauwerk'):
                    discrepancies.append({
                        'bahnhof': result['bahnhof'],
                        'name': result['name'],
                        'ril100': result['ril100'],
                        'category': result['category'],
                        'levels': result['levels'],
                        'tp_id': detail['tp_id'],
                        'tp_name': detail['tp_name'],
                        'tp_type': detail['tp_type'],
                        'issue': 'no staircase/elevator/escalator in equipment data',
                    })

    # Name/type contradictions: name says Unterführung but type says Überführung or vice versa
    under_kw = re.compile(r'(unterf[üu]hrung|\btunnel\b|personentunnel)', re.IGNORECASE)
    over_kw = re.compile(r'(personen[üu]berf[üu]hrung|fu[ßs]g[äa]ngerbrücke|personensteg|personenbrücke)', re.IGNORECASE)
    results_by_bf = {r['bahnhof']: r for r in results}
    seen_contradictions = set()
    for bf, details in tp_type_details.items():
        for detail in details:
            tp_id = detail['tp_id']
            if tp_id in seen_contradictions:
                continue
            tp_name = detail['tp_name']
            tp_type = detail['tp_type']
            issue = None
            if tp_type == 'Personenüberführung' and under_kw.search(tp_name):
                issue = 'name says Unterführung but typed as Überführung'
            elif tp_type == 'Personenunterführung' and over_kw.search(tp_name):
                issue = 'name says Überführung but typed as Unterführung'
            if issue and bf in results_by_bf:
                seen_contradictions.add(tp_id)
                station_info = results_by_bf[bf]
                discrepancies.append({
                    'bahnhof': bf,
                    'name': station_info['name'],
                    'ril100': station_info['ril100'],
                    'category': station_info['category'],
                    'levels': station_info['levels'],
                    'tp_id': tp_id,
                    'tp_name': tp_name,
                    'tp_type': tp_type,
                    'issue': issue,
                })

    # Flag stations with 0 platforms (data gap or decommissioned)
    stations_with_platforms = set()
    for row in all_platforms:
        bf = row.get('bahnhof', '').strip()
        if bf:
            stations_with_platforms.add(bf)
    for result in results:
        bf = result['bahnhof']
        if bf not in stations_with_platforms:
            discrepancies.append({
                'bahnhof': bf,
                'name': result['name'],
                'ril100': result['ril100'],
                'category': result['category'],
                'levels': result['levels'],
                'tp_id': '',
                'tp_name': '',
                'tp_type': '',
                'issue': 'no platform recorded in equipment data',
            })

    # Flag stations where Kellergeschoss/Untergeschoss is the only below-ground signal (ambiguous)
    keller_pattern = re.compile(r'(kellergeschoss|untergeschoss)', re.IGNORECASE)
    for result in results:
        bf = result['bahnhof']
        all_tp_names = tp_names_by_station.get(bf, []) + [r.get('name', '') for r in ramps_by_station.get(bf, [])]
        has_keller = any(keller_pattern.search(n) for n in all_tp_names)
        has_other_underpass = any(UNDERPASS_PATTERN.search(n) for n in all_tp_names) or (bf in station_has_underpass)
        if has_keller and not has_other_underpass:
            discrepancies.append({
                'bahnhof': bf,
                'name': result['name'],
                'ril100': result['ril100'],
                'category': result['category'],
                'levels': result['levels'],
                'tp_id': '',
                'tp_name': 'Kellergeschoss',
                'tp_type': '',
                'issue': 'Kellergeschoss/Untergeschoss present — may be building basement or passenger level',
            })

    with open(discrepancies_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'bahnhof', 'name', 'ril100', 'category', 'levels', 'tp_id', 'tp_name', 'tp_type', 'issue'
        ])
        writer.writeheader()
        writer.writerows(discrepancies)

    # Summary
    print(f"\n  Discrepancies for review: {discrepancies_path} ({len(discrepancies)} entries)")

    counts = {}
    for result in results:
        counts[result['levels']] = counts.get(result['levels'], 0) + 1

    print(f"\nOutput: {args.output}")
    print(f"Total: {len(results)} stations")
    for level_label in sorted(counts):
        print(f"  {level_label:>7}: {counts[level_label]:>5} ({counts[level_label]/len(results)*100:.1f}%)")


if __name__ == '__main__':
    main()
