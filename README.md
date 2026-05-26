# PeSDR

Piloto de SDR integrado para operações de marketing digital.

Plataforma multi-tenant de SDR conversacional via WhatsApp, com fluxos declarativos (TreeFlow) e adapters de CRM/messaging plugáveis. Spec completo em `docs/superpowers/specs/2026-05-21-ai-sdr-design.md`.

## Colaboradores

- [@NicolasAmaral1](https://github.com/NicolasAmaral1) — branch `dev/nicolas`
- [@pedrooaranda](https://github.com/pedrooaranda) — branch `dev/pedro`

## Fluxo

Cada colaborador trabalha em sua branch (`dev/nicolas`, `dev/pedro`) e abre PR para `main` quando o trabalho estiver pronto para revisão.

## Quickstart (foundation — Plano 1)

Pré-requisitos: `docker`, `uv`, `age`, `sops`.

```bash
# 1. Install deps
make install

# 2. Start postgres + redis (containers ai_sdr_postgres / ai_sdr_redis)
make up

# 3. Apply migrations
make migrate

# 4. Sua chave age pra dev local (se nunca gerou)
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
# Adicione sua public key ao .sops.yaml

# 5. Run app
uv run uvicorn ai_sdr.main:app --host 0.0.0.0 --port 8200 --reload

# 6. Hit /health
curl http://localhost:8200/health
```

## Testing

```bash
make test-unit             # rápido, sem docker
make test-integration      # precisa de `make up`
make test                  # ambos
```

## Estrutura

Detalhes em `docs/superpowers/plans/2026-05-21-foundation-multitenancy.md`.

## VPS (deploy de dev)

O projeto roda na `vps-nova` em `/root/PeSDR`. Portas custom pra evitar clash:
- Postgres: `15432`
- Redis: `16379`
- API: `8200` (futuramente atrás de Traefik em `sdr.luminai.ia.br`)
