import os
from datetime import date

from supabase import create_client, Client


GRADUATION_THRESHOLDS: dict[str, dict] = {
    'entity_dedup':          {'min_samples': 20, 'error_rate_max': 0.05},
    'location_dedup':        {'min_samples': 20, 'error_rate_max': 0.05},
    'temporal_dedup':        {'min_samples': 15, 'error_rate_max': 0.08},
    'entity_extraction':     {'min_samples': 25, 'error_rate_max': 0.03},
    'confidence_threshold':  {'min_samples': 30, 'error_rate_max': 0.10},
    'role_assignment':       {'min_samples': 20, 'error_rate_max': 0.05},
    'classification':        {'min_samples': 50, 'error_rate_max': 0.08},
    'severity':              {'min_samples': 50, 'error_rate_max': 0.10},
}


def _get_client() -> Client:
    return create_client(
        os.environ['SUPABASE_URL'],
        os.environ['SUPABASE_SECRET_KEY'],
    )


def get_autonomy_status() -> dict:
    """
    Query training_signals to compute per-category error rates.

    For each autonomy_signal:
    - total_decisions: count of training signals with this signal type
    - operator_corrections: count where operator changed the agent decision
    - error_rate: operator_corrections / total_decisions
    - samples_needed: max(0, threshold.min_samples - total_decisions)
    - graduated: total_decisions >= min_samples AND error_rate <= error_rate_max
    - status: 'graduated' | 'in_training' | 'insufficient_data'

    Returns dict keyed by autonomy_signal with above fields.
    """
    client = _get_client()

    resp = (
        client.table('training_signals')
        .select('operator_changes')
        .execute()
    )
    rows = resp.data or []

    totals:      dict[str, int] = {s: 0 for s in GRADUATION_THRESHOLDS}
    corrections: dict[str, int] = {s: 0 for s in GRADUATION_THRESHOLDS}

    for row in rows:
        oc = row.get('operator_changes') or {}
        signal = oc.get('autonomy_signal')
        if signal not in GRADUATION_THRESHOLDS:
            continue
        totals[signal] += 1
        # dismiss_reason_category present = operator explicitly rejected the agent decision
        if oc.get('dismiss_reason_category') is not None:
            corrections[signal] += 1

    result: dict[str, dict] = {}
    for signal, threshold in GRADUATION_THRESHOLDS.items():
        total      = totals[signal]
        correction = corrections[signal]
        error_rate = correction / total if total > 0 else 0.0
        min_samples    = threshold['min_samples']
        error_rate_max = threshold['error_rate_max']
        samples_needed = max(0, min_samples - total)

        if total == 0:
            status = 'insufficient_data'
        elif total >= min_samples and error_rate <= error_rate_max:
            status = 'graduated'
        else:
            status = 'in_training'

        result[signal] = {
            'total_decisions':      total,
            'operator_corrections': correction,
            'error_rate':           round(error_rate, 4),
            'samples_needed':       samples_needed,
            'graduated':            status == 'graduated',
            'status':               status,
        }

    return result


def get_graduation_report() -> str:
    """
    Human-readable graduation report for War Room.

    Format:
    AUTONOMY GRADUATION REPORT — [date]

    ✅ GRADUATED (agent can act autonomously):
       entity_dedup — error rate 2.1% (42 samples)

    ⏳ IN TRAINING (accumulating samples):
       location_dedup — error rate 4.8% (12/20 samples needed)

    ❌ INSUFFICIENT DATA:
       confidence_threshold — 3/30 samples

    Overall readiness: 1/8 categories graduated
    """
    status_map = get_autonomy_status()
    today = date.today().isoformat()

    graduated    = [(s, d) for s, d in status_map.items() if d['status'] == 'graduated']
    in_training  = [(s, d) for s, d in status_map.items() if d['status'] == 'in_training']
    insufficient = [(s, d) for s, d in status_map.items() if d['status'] == 'insufficient_data']

    lines = [f"AUTONOMY GRADUATION REPORT — {today}", ""]

    if graduated:
        lines.append("✅ GRADUATED (agent can act autonomously):")
        for signal, d in graduated:
            lines.append(
                f"   {signal} — error rate {d['error_rate'] * 100:.1f}%"
                f" ({d['total_decisions']} samples)"
            )
    else:
        lines.append("✅ GRADUATED: none yet")
    lines.append("")

    if in_training:
        lines.append("⏳ IN TRAINING (accumulating samples):")
        for signal, d in in_training:
            total   = d['total_decisions']
            needed  = GRADUATION_THRESHOLDS[signal]['min_samples']
            lines.append(
                f"   {signal} — error rate {d['error_rate'] * 100:.1f}%"
                f" ({total}/{needed} samples needed)"
            )
    else:
        lines.append("⏳ IN TRAINING: none")
    lines.append("")

    if insufficient:
        lines.append("❌ INSUFFICIENT DATA:")
        for signal, d in insufficient:
            needed = GRADUATION_THRESHOLDS[signal]['min_samples']
            lines.append(f"   {signal} — {d['total_decisions']}/{needed} samples")
    else:
        lines.append("❌ INSUFFICIENT DATA: none")
    lines.append("")

    grad_count  = len(graduated)
    total_count = len(GRADUATION_THRESHOLDS)
    lines.append(f"Overall readiness: {grad_count}/{total_count} categories graduated")

    return "\n".join(lines)
