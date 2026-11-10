# Teste de capacidade

**Pergunta:** os backends do portfólio cabem juntos numa VM Oracle Cloud Always Free (Ampere A1, 2 OCPUs e 12 GB) com folga para picos?

**Regra:** nenhuma decisão de hospedagem é tomada por estimativa. Primeiro se mede.

## Como o teste funciona

1. **Teto da VM num cgroup.** `capacity/scripts/setup-cgroup.sh` cria um cgroup v2 com `cpu.max = 200000 100000` (2 CPUs) e `memory.max = 12 GiB`, sem swap. Todos os contêineres da stack entram nele via `cgroup_parent`, então o limite vale para a **soma** deles, como numa VM, e não por contêiner.
2. **Stack como iria para produção.** `capacity/tripflow/compose.yml` usa a imagem de produção do TripFlow (FrankenPHP, sem dependências de desenvolvimento) e roda API, worker, relay da outbox, consumidor, MySQL 8.4, Redis 7 e RabbitMQ 4. Só a API expõe porta, e só em `127.0.0.1`.
3. **Dados sem Faker.** O `DatabaseSeeder` do TripFlow depende de Faker, que não existe na imagem de produção. `capacity/tripflow/seed.php` cria uma empresa, 5 centros de custo, 40 viajantes e 10 aprovadores com valores fixos.
4. **Carga de fora do teto.** O k6 roda no host, fora do cgroup, para não disputar a CPU medida. Cada iteração percorre o fluxo inteiro: criar com `Idempotency-Key`, enviar, aprovar (lock do centro de custo e outbox), emitir reservas (lock no Redis, fornecedor simulado e outbox) e listar. A taxa sobe em degraus (padrão: 2, 5, 10 e 15 iterações/s).
5. **Amostragem.** `capacity/scripts/sample.sh` lê o cgroup a cada 2 s (`memory.current`, `cpu.stat`, `io.stat`) e `docker stats` a cada 10 s, marcando a fase (boot, ociosa, carga, drenagem). No fim registra `memory.peak` e `memory.events` do kernel.
6. **Consistência.** Depois da carga, o teste espera 30 s e confere se a outbox drenou, quantas mensagens a inbox processou e quantas solicitações ficaram em cada status.
7. **Relatório e critérios.** `capacity/scripts/report.py` gera o relatório no resumo do job e em `results/report.md`. O job reprova se houver OOM no cgroup, contêiner reiniciado, mais de 1% de erros em algum degrau ou evento não publicado na outbox.

## O que este teste prova e o que não prova

| Prova | Não prova |
| --- | --- |
| Pico de memória da stack inteira sob carga, medido pelo kernel | Desempenho de CPU da Ampere A1: o runner do GitHub é x86 e tem outra frequência e microarquitetura |
| Se algo é morto por falta de memória com o teto aplicado | Comportamento com mais de um backend ao mesmo tempo, até os outros entrarem no Compose |
| Latência p50/p95/p99 e erros por degrau, com o teto de CPU aplicado | Latência real de internet entre visitante e VM |
| Escrita em disco e tamanho dos volumes depois da carga | Desempenho do disco da Oracle (block volume) |
| Que a outbox, o relay e o consumidor acompanham a carga | Disponibilidade da Oracle Always Free |

Por isso o mesmo teste roda de novo **na VM A1**, antes de qualquer demo ficar pública.

## Rodar

- **Pelo GitHub:** Actions → *Teste de capacidade* → *Run workflow*. Em PRs que mexem em `capacity/`, roda com degraus de 60 s.
- **Localmente (Linux com cgroup v2 e Docker):**

```bash
git clone https://github.com/acamilateixeira/Tripflow ../tripflow
sudo -E bash capacity/scripts/setup-cgroup.sh 2 12        # imprime CGROUP_PARENT e CGROUP_PATH
export TRIPFLOW_SRC=$PWD/../tripflow CGROUP_PARENT=... CGROUP_PATH=... COMPOSE_FILE=capacity/tripflow/compose.yml
export APP_KEY="base64:$(openssl rand -base64 32)"
docker compose up -d --build --wait
docker compose exec -T api php artisan migrate --force
docker compose exec -T api php /capacity/seed.php
```

## Próximos backends

Emppo Finance e PulseOps entram neste Compose quando estiverem prontos, e o teste passa a medir os quatro juntos. Até lá, o resultado vale só para o TripFlow, e o relatório diz isso no título.
