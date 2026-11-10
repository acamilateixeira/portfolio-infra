#!/usr/bin/env python3
"""Gera o relatório do teste de capacidade a partir das amostras e do resumo do k6.

Entradas em RESULTS (padrão: results/):
  meta.json          ambiente, teto do cgroup, início da carga e degraus
  cgroup.csv         amostras do cgroup pai (sample.sh)
  containers.jsonl   docker stats por contêiner (sample.sh)
  cgroup-final.txt   memory.peak, eventos de memória e cpu.stat ao final
  k6-summary.json    resumo do k6 (handleSummary)
  disk.txt           uso de disco dos volumes e imagens

Saídas: report.md (também vai para o resumo do job) e report.json.
Sem dependências além da biblioteca padrão.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

RESULTS = Path(os.environ.get("RESULTS", "results"))
MIB = 1024 * 1024


def load_meta() -> dict:
    return json.loads((RESULTS / "meta.json").read_text())


def load_samples() -> list[dict]:
    rows = []
    with (RESULTS / "cgroup.csv").open() as fh:
        for row in csv.DictReader(fh):
            try:
                rows.append({
                    "t": int(row["epoch_ms"]),
                    "phase": row["phase"],
                    "mem": int(row["memory_current"]),
                    "cpu_usec": int(row["cpu_usage_usec"]),
                    "throttled": int(row["nr_throttled"] or 0),
                    "throttled_usec": int(row["throttled_usec"] or 0),
                    "io_r": int(row["io_rbytes"] or 0),
                    "io_w": int(row["io_wbytes"] or 0),
                })
            except (ValueError, KeyError):
                continue  # linha cortada no fim da amostragem
    return rows


def final_values() -> dict[str, str]:
    values = {}
    path = RESULTS / "cgroup-final.txt"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def window_stats(samples: list[dict], cpus: float) -> dict | None:
    if len(samples) < 2:
        return None
    first, last = samples[0], samples[-1]
    seconds = (last["t"] - first["t"]) / 1000
    if seconds <= 0:
        return None
    # CPU instantânea entre amostras consecutivas, em % do teto (100% = todas as CPUs do teto).
    instant = []
    for a, b in zip(samples, samples[1:]):
        dt = (b["t"] - a["t"]) / 1000
        if dt > 0:
            instant.append((b["cpu_usec"] - a["cpu_usec"]) / 1e6 / dt / cpus * 100)
    return {
        "seconds": round(seconds, 1),
        "cpu_avg_pct_of_cap": round((last["cpu_usec"] - first["cpu_usec"]) / 1e6 / seconds / cpus * 100, 1),
        "cpu_peak_pct_of_cap": round(max(instant), 1) if instant else None,
        "throttled_periods": last["throttled"] - first["throttled"],
        "throttled_seconds": round((last["throttled_usec"] - first["throttled_usec"]) / 1e6, 2),
        "mem_peak_mib": round(max(s["mem"] for s in samples) / MIB),
        "mem_avg_mib": round(sum(s["mem"] for s in samples) / len(samples) / MIB),
        "io_read_mib": round((last["io_r"] - first["io_r"]) / MIB, 1),
        "io_write_mib": round((last["io_w"] - first["io_w"]) / MIB, 1),
    }


def parse_mib(text: str) -> float | None:
    match = re.match(r"\s*([\d.]+)\s*([KMGT]?i?B)", text)
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2)
    scale = {"B": 1 / MIB, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024, "TiB": 1024 * 1024}
    return value * scale.get(unit, 1)


def container_peaks() -> list[dict]:
    peaks: dict[str, dict] = {}
    path = RESULTS / "containers.jsonl"
    if not path.exists():
        return []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = row.get("Name", "?")
        mem = parse_mib(row.get("MemUsage", "").split("/")[0])
        cpu = float(row.get("CPUPerc", "0%").rstrip("%") or 0)
        entry = peaks.setdefault(name, {"name": name, "mem_peak_mib": 0.0, "cpu_peak_pct_one_core": 0.0, "load_mem_samples": []})
        if mem is not None:
            entry["mem_peak_mib"] = max(entry["mem_peak_mib"], mem)
            if row.get("phase") == "load":
                entry["load_mem_samples"].append(mem)
        entry["cpu_peak_pct_one_core"] = max(entry["cpu_peak_pct_one_core"], cpu)
    out = []
    for entry in peaks.values():
        samples = entry.pop("load_mem_samples")
        entry["mem_avg_load_mib"] = round(sum(samples) / len(samples)) if samples else None
        entry["mem_peak_mib"] = round(entry["mem_peak_mib"])
        out.append(entry)
    return sorted(out, key=lambda e: -e["mem_peak_mib"])


def k6_stages(meta: dict) -> list[dict]:
    path = RESULTS / "k6-summary.json"
    if not path.exists():
        return []
    metrics = json.loads(path.read_text()).get("metrics", {})
    stages = []
    for rate in meta["rates"]:
        name = f"rate_{rate:02d}"
        duration = metrics.get(f"http_req_duration{{scenario:{name}}}", {}).get("values", {})
        failed = metrics.get(f"http_req_failed{{scenario:{name}}}", {}).get("values", {})
        reqs = metrics.get(f"http_reqs{{scenario:{name}}}", {}).get("values", {})
        iters = metrics.get(f"iterations{{scenario:{name}}}", {}).get("values", {})
        stages.append({
            "stage": name,
            "target_iterations_per_s": rate,
            "iterations": int(iters.get("count", 0)),
            "requests": int(reqs.get("count", 0)),
            "req_per_s": round(reqs.get("count", 0) / meta["stage_seconds"], 1),
            "p50_ms": round(duration.get("med", 0), 1),
            "p95_ms": round(duration.get("p(95)", 0), 1),
            "p99_ms": round(duration.get("p(99)", 0), 1),
            "max_ms": round(duration.get("max", 0), 1),
            "error_rate_pct": round(failed.get("rate", 0) * 100, 2),
        })
    return stages


def k6_endpoints() -> list[dict]:
    path = RESULTS / "k6-summary.json"
    if not path.exists():
        return []
    metrics = json.loads(path.read_text()).get("metrics", {})
    rows = []
    for step in ["create", "submit", "approve", "book", "list"]:
        values = metrics.get(f"http_req_duration{{name:{step}}}", {}).get("values", {})
        if values:
            rows.append({"endpoint": step, "p50_ms": round(values.get("med", 0), 1), "p95_ms": round(values.get("p(95)", 0), 1), "p99_ms": round(values.get("p(99)", 0), 1)})
    dropped = metrics.get("dropped_iterations", {}).get("values", {}).get("count", 0)
    checks = metrics.get("checks", {}).get("values", {})
    return rows + [{"dropped_iterations": int(dropped), "checks_pass_rate_pct": round(checks.get("rate", 0) * 100, 2)}]


def main() -> int:
    meta = load_meta()
    samples = load_samples()
    cpus = float(meta["cpus"])
    final = final_values()
    load_start = meta.get("load_start_ms")
    summary_path = RESULTS / "k6-summary.json"
    if summary_path.exists():
        # Os degraus começam depois dos logins do setup; o k6 registra esse instante.
        started = json.loads(summary_path.read_text()).get("setup_data", {}) or {}
        load_start = started.get("scenariosStartMs") or load_start

    windows = {
        "idle": window_stats([s for s in samples if s["phase"] == "idle"], cpus),
        "load (total)": window_stats([s for s in samples if s["phase"] == "load"], cpus),
    }
    if load_start:
        for index, rate in enumerate(meta["rates"]):
            start = load_start + index * meta["stage_seconds"] * 1000
            end = start + meta["stage_seconds"] * 1000
            windows[f"rate_{rate:02d}"] = window_stats([s for s in samples if s["phase"] == "load" and start <= s["t"] < end], cpus)

    peak_kernel = final.get("memory_peak", "unavailable")
    oom_kills = int(final.get("memory_events_oom_kill", "0") or 0)
    report = {
        "meta": meta,
        "kernel": {
            "memory_peak_mib": round(int(peak_kernel) / MIB) if peak_kernel.isdigit() else None,
            "memory_max_mib": round(int(final["memory_max"]) / MIB) if final.get("memory_max", "").isdigit() else final.get("memory_max"),
            "oom_kills": oom_kills,
            "memory_high_events": int(final.get("memory_events_high", "0") or 0),
            "cpu_throttled_seconds_total": round(int(final.get("cpu_stat_throttled_usec", "0") or 0) / 1e6, 2),
        },
        "windows": windows,
        "containers": container_peaks(),
        "stages": k6_stages(meta),
        "endpoints": k6_endpoints(),
        "disk": (RESULTS / "disk.txt").read_text() if (RESULTS / "disk.txt").exists() else "",
    }
    (RESULTS / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (RESULTS / "report.md").write_text(render(report))
    print(render(report))
    return 0


def render(r: dict) -> str:
    m, k = r["meta"], r["kernel"]
    lines = [
        f"# Teste de capacidade — {', '.join(m['stack'])}",
        "",
        f"- **Data:** {m['date']} · **commit do harness:** `{m['harness_commit']}` · **TripFlow:** `{m['tripflow_commit']}`",
        f"- **Ambiente:** {m['runner']} ({m['arch']}, {m['host_cpus']} vCPUs, {m['host_mem_gib']} GiB) — **não é a VM Oracle A1 (Arm)**; memória e disco se comparam bem, CPU só aproximadamente.",
        f"- **Teto aplicado a todos os contêineres juntos (cgroup):** {m['cpus']} CPUs, {m['mem_gib']} GiB, sem swap.",
        f"- **Carga:** k6 fora do teto, degraus de {', '.join(str(x) for x in m['rates'])} iterações/s por {m['stage_seconds']} s. Cada iteração = criar, enviar, aprovar, emitir e listar.",
        "",
        "## Resultado do kernel",
        "",
        "| Pico de memória (memory.peak) | Teto | OOM kills | Tempo de CPU estrangulado |",
        "| --- | --- | --- | --- |",
        f"| {k['memory_peak_mib']} MiB | {k['memory_max_mib']} MiB | {k['oom_kills']} | {k['cpu_throttled_seconds_total']} s |",
        "",
        "## Por janela (cgroup pai)",
        "",
        "| Janela | Duração | CPU média (% do teto) | CPU pico | Throttling | Memória pico | Memória média | Leitura | Escrita |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, w in r["windows"].items():
        if w:
            lines.append(f"| {name} | {w['seconds']} s | {w['cpu_avg_pct_of_cap']}% | {w['cpu_peak_pct_of_cap']}% | {w['throttled_seconds']} s | {w['mem_peak_mib']} MiB | {w['mem_avg_mib']} MiB | {w['io_read_mib']} MiB | {w['io_write_mib']} MiB |")
    lines += ["", "## Latência e erros por degrau (k6)", "", "| Degrau | Iterações | Req/s | p50 | p95 | p99 | máx | Erros |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in r["stages"]:
        lines.append(f"| {s['target_iterations_per_s']} it/s | {s['iterations']} | {s['req_per_s']} | {s['p50_ms']} ms | {s['p95_ms']} ms | {s['p99_ms']} ms | {s['max_ms']} ms | {s['error_rate_pct']}% |")
    endpoints = [e for e in r["endpoints"] if "endpoint" in e]
    extra = next((e for e in r["endpoints"] if "dropped_iterations" in e), {})
    if endpoints:
        lines += ["", "## Latência por endpoint (todos os degraus)", "", "| Endpoint | p50 | p95 | p99 |", "| --- | --- | --- | --- |"]
        lines += [f"| {e['endpoint']} | {e['p50_ms']} ms | {e['p95_ms']} ms | {e['p99_ms']} ms |" for e in endpoints]
        lines += ["", f"Iterações descartadas pelo k6 (VUs insuficientes): {extra.get('dropped_iterations', 0)} · checks aprovados: {extra.get('checks_pass_rate_pct', 0)}%"]
    lines += ["", "## Por contêiner (docker stats, amostra a cada 10 s)", "", "| Contêiner | Memória pico | Memória média sob carga | CPU pico (% de 1 núcleo) |", "| --- | --- | --- | --- |"]
    lines += [f"| {c['name']} | {c['mem_peak_mib']} MiB | {c['mem_avg_load_mib']} MiB | {c['cpu_peak_pct_one_core']}% |" for c in r["containers"]]
    if r["disk"]:
        lines += ["", "## Disco", "", "```", r["disk"].strip(), "```"]
    lines += ["", "Amostras brutas (cgroup.csv, containers.jsonl, k6-summary.json) estão no artefato do job."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
