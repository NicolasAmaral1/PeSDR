"""RD Station OAuth2 — Authorization Code flow inicial pra obter refresh_token.

Uso (rode UMA vez por tenant que vai integrar RD Station):

    uv run python demos/.../scripts/oauth_flow_init.py \\
        --client-id <id> \\
        --client-secret <secret> \\
        --redirect-uri http://localhost:8765/oauth_callback

Roteiro:
1. Constrói URL de autorização e abre no browser default
2. Operador autoriza no painel RD Station
3. RD Station redireciona pra http://localhost:8765/oauth_callback?code=<auth_code>
4. Script captura code via servidor HTTP local efêmero
5. Troca auth_code por (access_token, refresh_token) via POST OAUTH_TOKEN_URL
6. Imprime refresh_token na stdout
7. Operador cifra: edita tenants/<slug>/secrets.enc.yaml com rdstation_refresh_token

STUB — implementação real na Fase B T5.
"""
from __future__ import annotations

import argparse
import sys


OAUTH_AUTHORIZE_URL = "https://api.rd.services/auth/dialog"
OAUTH_TOKEN_URL = "https://api.rd.services/auth/token"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RD Station OAuth2 Authorization Code flow — obter refresh_token."
    )
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument(
        "--redirect-uri",
        default="http://localhost:8765/oauth_callback",
        help="URL local pra captura do code. Default: localhost:8765.",
    )
    args = parser.parse_args()

    print(
        f"[skeleton] Iniciaria fluxo OAuth com:",
        f"  client_id     = {args.client_id}",
        f"  client_secret = ***",
        f"  redirect_uri  = {args.redirect_uri}",
        sep="\n",
    )
    print()
    print("Fluxo real (implementação Fase B T5):")
    print(
        "  1. Build URL: GET https://api.rd.services/auth/dialog"
        "?client_id={id}&redirect_uri={uri}",
    )
    print("  2. webbrowser.open(url) — abre no browser default")
    print(
        "  3. http.server local efêmero captura code=<...> em "
        "GET /oauth_callback?code=<auth_code>",
    )
    print(
        "  4. POST https://api.rd.services/auth/token com "
        "{client_id, client_secret, code, grant_type='authorization_code'}",
    )
    print("  5. Print refresh_token na stdout")
    print()
    print("→ Operador edita secrets.enc.yaml e adiciona rdstation_refresh_token")
    return 0


if __name__ == "__main__":
    sys.exit(main())
