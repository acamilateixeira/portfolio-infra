#!/usr/bin/env bash
# Amostra o cgroup pai (todos os contêineres juntos) e cada contêiner.
#
#   cgroup.csv     a cada INTERVAL s: memória atual, CPU acumulada, throttling e I/O
#   containers.jsonl a cada CONTAINER_INTERVAL s: `docker stats` de cada contêiner
#
# Para quando o arquivo $OUT/stop existir. Uso:
#   CGROUP_PATH=... OUT=results capacity/scripts/sample.sh &
set -uo pipefail

CG="${CGROUP_PATH:?defina CGROUP_PATH}"
OUT="${OUT:-results}"
INTERVAL="${INTERVAL:-2}"
CONTAINER_INTERVAL="${CONTAINER_INTERVAL:-10}"
mkdir -p "$OUT"

echo "epoch_ms,phase,memory_current,cpu_usage_usec,nr_throttled,throttled_usec,io_rbytes,io_wbytes" > "$OUT/cgroup.csv"
: > "$OUT/containers.jsonl"

io_totals() {
  # Soma rbytes e wbytes de todos os dispositivos em io.stat.
  awk '{for (i = 2; i <= NF; i++) { split($i, kv, "="); if (kv[1] == "rbytes") r += kv[2]; if (kv[1] == "wbytes") w += kv[2] }} END { printf "%d,%d", r, w }' "$CG/io.stat" 2>/dev/null || printf '0,0'
}

stat_field() { awk -v k="$1" '$1 == k { print $2 }' "$CG/cpu.stat"; }

last_containers=0
while [ ! -e "$OUT/stop" ]; do
  now=$(date +%s%3N)
  phase=$(cat "$OUT/phase" 2>/dev/null || echo unknown)
  printf '%s,%s,%s,%s,%s,%s,%s\n' "$now" "$phase" "$(cat "$CG/memory.current")" \
    "$(stat_field usage_usec)" "$(stat_field nr_throttled)" "$(stat_field throttled_usec)" "$(io_totals)" >> "$OUT/cgroup.csv"

  if [ $((now - last_containers)) -ge $((CONTAINER_INTERVAL * 1000)) ]; then
    last_containers=$now
    docker stats --no-stream --format '{{json .}}' 2>/dev/null \
      | sed "s/^{/{\"epoch_ms\":$now,\"phase\":\"$phase\",/" >> "$OUT/containers.jsonl"
  fi
  sleep "$INTERVAL"
done

# Valores do kernel que não dependem do intervalo de amostragem.
{
  echo "memory_peak=$(cat "$CG/memory.peak" 2>/dev/null || echo unavailable)"
  echo "memory_max=$(cat "$CG/memory.max")"
  echo "cpu_max=$(cat "$CG/cpu.max")"
  sed 's/^/memory_events_/; s/ /=/' "$CG/memory.events"
  sed 's/^/cpu_stat_/; s/ /=/' "$CG/cpu.stat"
} > "$OUT/cgroup-final.txt"
