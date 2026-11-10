#!/usr/bin/env bash
# Cria o cgroup pai que impõe o teto da VM Oracle A1 a TODOS os contêineres
# juntos: 2 CPUs (cpu.max 200000/100000) e 12 GiB de memória, sem swap.
#
# Funciona com os dois drivers de cgroup do Docker:
#   - systemd: uma slice "capacity.slice";
#   - cgroupfs: um diretório /sys/fs/cgroup/capacity.
#
# Escreve CGROUP_PARENT (para o compose) e CGROUP_PATH (para o amostrador) em
# $GITHUB_ENV, ou imprime na saída padrão fora do Actions.
#
# Uso: sudo -E capacity/scripts/setup-cgroup.sh [cpus] [memória em GiB]
set -euo pipefail

CPUS="${1:-2}"
MEM_GIB="${2:-12}"
MEM_BYTES=$((MEM_GIB * 1024 * 1024 * 1024))

if [ "$(stat -fc %T /sys/fs/cgroup)" != "cgroup2fs" ]; then
  echo "cgroup v2 é obrigatório (encontrado: $(stat -fc %T /sys/fs/cgroup))" >&2
  exit 1
fi

DRIVER="$(docker info --format '{{.CgroupDriver}}')"
echo "Driver de cgroup do Docker: $DRIVER"

if [ "$DRIVER" = "systemd" ]; then
  cat > /etc/systemd/system/capacity.slice <<EOF
[Unit]
Description=Teto da VM Oracle A1 para o teste de capacidade

[Slice]
CPUAccounting=yes
MemoryAccounting=yes
IOAccounting=yes
CPUQuota=$((CPUS * 100))%
MemoryMax=${MEM_BYTES}
MemorySwapMax=0
EOF
  systemctl daemon-reload
  systemctl start capacity.slice
  PARENT="capacity.slice"
  CG="/sys/fs/cgroup/capacity.slice"
else
  CG="/sys/fs/cgroup/capacity"
  mkdir -p "$CG"
  for c in cpu memory io; do
    grep -qw "$c" /sys/fs/cgroup/cgroup.subtree_control || echo "+$c" > /sys/fs/cgroup/cgroup.subtree_control
  done
  echo "$((CPUS * 100000)) 100000" > "$CG/cpu.max"
  echo "$MEM_BYTES" > "$CG/memory.max"
  echo 0 > "$CG/memory.swap.max" 2>/dev/null || true
  PARENT="/capacity"
fi

echo "cpu.max:    $(cat "$CG/cpu.max")"
echo "memory.max: $(cat "$CG/memory.max")"

if [ -n "${GITHUB_ENV:-}" ]; then
  { echo "CGROUP_PARENT=$PARENT"; echo "CGROUP_PATH=$CG"; } >> "$GITHUB_ENV"
else
  echo "CGROUP_PARENT=$PARENT"
  echo "CGROUP_PATH=$CG"
fi
