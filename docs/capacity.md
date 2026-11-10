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

## Resultado: TripFlow, 10/11/2026

[Execução no GitHub Actions](https://github.com/acamilateixeira/portfolio-infra/actions/runs/42877175921) com degraus de 120 s, TripFlow em `53cde77`, runner x86 com 4 vCPUs. **Aprovado nos critérios** (sem OOM, sem reinício, 0% de erros, outbox drenada), mas a CPU satura no último degrau.

| Degrau | Req/s | p50 | p95 | p99 | Erros | CPU média (% de 2 CPUs) | Memória pico |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Ociosa | — | — | — | — | — | 8,1% | 722 MiB |
| 2 it/s | 10 | 20 ms | 29 ms | 70 ms | 0% | 20,3% | 810 MiB |
| 5 it/s | 25 | 20 ms | 33 ms | 76 ms | 0% | 38,7% | 833 MiB |
| 10 it/s | 50 | 21 ms | 158 ms | 209 ms | 0% | 71,4% | 874 MiB |
| 15 it/s | 63 | 2.300 ms | 2.984 ms | 3.161 ms | 0% | 99,9% | 936 MiB |

- **Memória não é o limite.** Pico do kernel (`memory.peak`) de **940 MiB** para a stack inteira, 7,7% do teto de 12 GiB. MySQL é o maior consumidor (476 MiB), seguido de RabbitMQ (158 MiB) e da API (79 MiB).
- **CPU é o gargalo.** Até 10 it/s (50 req/s) o p95 fica abaixo de 160 ms. A 15 it/s a CPU chega a 100% do teto, o kernel estrangula 178 s de CPU no degrau, o p95 vai a 3 s e o k6 descarta 288 iterações por falta de VUs. A API (FrankenPHP) chega a 160% de um núcleo.
- **Consistência sob carga:** 3.555 solicitações emitidas, 10.665 eventos publicados pela outbox (3 por fluxo), 7.110 processados pela inbox (os 2 tipos que o consumidor assina) e **0 pendentes** 30 s depois.
- **Disco:** 309 MB no volume do MySQL depois de 3.555 fluxos completos; 1,25 GB escritos durante a carga.

**O que isso muda na proposta:** memória sobra para Emppo e PulseOps na mesma VM; a CPU de 2 OCPUs é o recurso a dividir. O tráfego esperado de um portfólio fica muito abaixo de 50 req/s, mas a demo pública precisa de rate limit para não deixar um visitante empurrar a VM para o degrau de 15 it/s. O número de CPU vale para x86: repetir na A1 antes de publicar.

**Limitação dos critérios:** o job aprova sem olhar latência. A 15 it/s ele passa com p95 de 3 s. Um critério de p95 por degrau entra quando definirmos a carga-alvo da demo.

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
