# portfolio-infra

**Português** · [English](#english)

Infraestrutura dos projetos do portfólio de [Camila Teixeira](https://acamilateixeira.github.io/camilat/): teste de capacidade, Compose de produção, backups e recuperação.

## Estado atual

| Item | Situação |
| --- | --- |
| [Teste de capacidade](docs/capacity.md) do TripFlow sob o teto da VM Oracle A1 (2 CPUs, 12 GiB) | Implementado e executado: pico de 940 MiB; CPU satura perto de 63 req/s ([resultado](docs/capacity.md#resultado-tripflow-10112026)) |
| Emppo Finance e PulseOps no mesmo teste | Planejado: entram quando os backends estiverem prontos |
| Compose de produção (limites, health checks, proxy HTTPS) | Planejado: depende do resultado da capacidade |
| Backups externos criptografados com restauração testada | Planejado |
| Deploy na Oracle Cloud | Não iniciado: exige conta e aprovação explícita |

Nada aqui cria recurso pago ou publica serviço. O deploy só acontece depois do teste de capacidade passar no runner e de novo na VM A1.

## English

Infrastructure for the projects in Camila Teixeira's portfolio: capacity testing, production Compose, backups, and recovery.

The first piece is a [capacity test](docs/capacity.md) that runs TripFlow's production image (API, worker, outbox relay, consumer, MySQL, Redis, RabbitMQ) inside a single cgroup capped at the Oracle Cloud Always Free A1 limits (2 CPUs, 12 GiB, no swap), drives it with k6 from outside the cap, and records CPU, memory, I/O, disk, latency, errors, and outbox drain. The GitHub runner is x86, so CPU results are approximate; the same test runs again on the A1 VM before anything is published.
