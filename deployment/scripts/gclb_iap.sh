#!/usr/bin/env bash
# Global External Application LB + IAP による Cloud Run 公開スクリプト.
#
# 使い方: deployment/scripts/gclb_iap.sh <subcommand>
#   setup        LB 一式 (IP / 証明書 / NEG / Backend / URL Map / Proxy / FR) を作成し IAP を有効化
#   dns          DNS_PROJECT の DNS_ZONE に A レコードを作成・更新
#   switch-run   Cloud Run の Native IAP を無効化し ingress を LB 経由のみに制限
#   oauth-client カスタム OAuth クライアント (外部アカウント許可用) を Backend Service に設定
#   add-user     IAP 権限付与 + アプリ側 IAP_USERS に追記 (IAP_USER 必須)
#   status       証明書・DNS・IAP ポリシーの状態を表示
#
# 必要な環境変数は Makefile から渡す (PROJECT_ID, REGION, SERVICE_NAME, DOMAIN, DNS_PROJECT, DNS_ZONE, LB_PREFIX).
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${SERVICE_NAME:?SERVICE_NAME is required}"
: "${DOMAIN:?DOMAIN is required}"
: "${DNS_PROJECT:?DNS_PROJECT is required}"
: "${DNS_ZONE:?DNS_ZONE is required}"
LB_PREFIX="${LB_PREFIX:-aiops}"

IP_NAME="${LB_PREFIX}-ip"
CERT_NAME="${LB_PREFIX}-cert"
NEG_NAME="${LB_PREFIX}-neg"
BACKEND_NAME="${LB_PREFIX}-backend"
URLMAP_NAME="${LB_PREFIX}-urlmap"
REDIRECT_URLMAP_NAME="${LB_PREFIX}-http-redirect"
HTTPS_PROXY_NAME="${LB_PREFIX}-https-proxy"
HTTP_PROXY_NAME="${LB_PREFIX}-http-proxy"
HTTPS_FR_NAME="${LB_PREFIX}-https-fr"
HTTP_FR_NAME="${LB_PREFIX}-http-fr"

GC=(gcloud --project="${PROJECT_ID}" --quiet)

log() { echo "==> $*"; }

# リソースが存在しなければ作成する (describe 成功時はスキップ)
ensure() {
  local label="$1" describe_cmd="$2"
  shift 2
  if eval "${describe_cmd}" >/dev/null 2>&1; then
    log "${label}: 既存のためスキップ"
  else
    log "${label}: 作成"
    "$@"
  fi
}

get_ip() {
  "${GC[@]}" compute addresses describe "${IP_NAME}" --global --format="value(address)"
}

cmd_setup() {
  ensure "静的 IP ${IP_NAME}" \
    "${GC[*]} compute addresses describe ${IP_NAME} --global" \
    "${GC[@]}" compute addresses create "${IP_NAME}" --global --ip-version=IPV4

  ensure "Managed 証明書 ${CERT_NAME}" \
    "${GC[*]} compute ssl-certificates describe ${CERT_NAME} --global" \
    "${GC[@]}" compute ssl-certificates create "${CERT_NAME}" --global --domains="${DOMAIN}"

  ensure "Serverless NEG ${NEG_NAME}" \
    "${GC[*]} compute network-endpoint-groups describe ${NEG_NAME} --region=${REGION}" \
    "${GC[@]}" compute network-endpoint-groups create "${NEG_NAME}" \
      --region="${REGION}" --network-endpoint-type=serverless --cloud-run-service="${SERVICE_NAME}"

  # Serverless NEG は portName / timeout 非対応 (タイムアウトは Cloud Run の request timeout が適用)
  ensure "Backend Service ${BACKEND_NAME}" \
    "${GC[*]} compute backend-services describe ${BACKEND_NAME} --global" \
    "${GC[@]}" compute backend-services create "${BACKEND_NAME}" \
      --global --load-balancing-scheme=EXTERNAL_MANAGED

  if ! "${GC[@]}" compute backend-services describe "${BACKEND_NAME}" --global \
      --format="value(backends[].group)" | grep -q "${NEG_NAME}"; then
    log "Backend に NEG を追加"
    "${GC[@]}" compute backend-services add-backend "${BACKEND_NAME}" \
      --global --network-endpoint-group="${NEG_NAME}" --network-endpoint-group-region="${REGION}"
  fi

  ensure "URL Map ${URLMAP_NAME}" \
    "${GC[*]} compute url-maps describe ${URLMAP_NAME} --global" \
    "${GC[@]}" compute url-maps create "${URLMAP_NAME}" --global --default-service="${BACKEND_NAME}"

  ensure "HTTPS Proxy ${HTTPS_PROXY_NAME}" \
    "${GC[*]} compute target-https-proxies describe ${HTTPS_PROXY_NAME} --global" \
    "${GC[@]}" compute target-https-proxies create "${HTTPS_PROXY_NAME}" \
      --global --url-map="${URLMAP_NAME}" --ssl-certificates="${CERT_NAME}"

  ensure "HTTPS Forwarding Rule ${HTTPS_FR_NAME}" \
    "${GC[*]} compute forwarding-rules describe ${HTTPS_FR_NAME} --global" \
    "${GC[@]}" compute forwarding-rules create "${HTTPS_FR_NAME}" \
      --global --load-balancing-scheme=EXTERNAL_MANAGED --address="${IP_NAME}" \
      --target-https-proxy="${HTTPS_PROXY_NAME}" --ports=443

  # HTTP -> HTTPS リダイレクト
  if ! "${GC[@]}" compute url-maps describe "${REDIRECT_URLMAP_NAME}" --global >/dev/null 2>&1; then
    log "HTTP リダイレクト URL Map ${REDIRECT_URLMAP_NAME}: 作成"
    local tmp
    tmp="$(mktemp)"
    cat >"${tmp}" <<EOF
name: ${REDIRECT_URLMAP_NAME}
defaultUrlRedirect:
  httpsRedirect: true
  redirectResponseCode: MOVED_PERMANENTLY_DEFAULT
EOF
    "${GC[@]}" compute url-maps import "${REDIRECT_URLMAP_NAME}" --global --source="${tmp}"
    rm -f "${tmp}"
  else
    log "HTTP リダイレクト URL Map ${REDIRECT_URLMAP_NAME}: 既存のためスキップ"
  fi

  ensure "HTTP Proxy ${HTTP_PROXY_NAME}" \
    "${GC[*]} compute target-http-proxies describe ${HTTP_PROXY_NAME} --global" \
    "${GC[@]}" compute target-http-proxies create "${HTTP_PROXY_NAME}" \
      --global --url-map="${REDIRECT_URLMAP_NAME}"

  ensure "HTTP Forwarding Rule ${HTTP_FR_NAME}" \
    "${GC[*]} compute forwarding-rules describe ${HTTP_FR_NAME} --global" \
    "${GC[@]}" compute forwarding-rules create "${HTTP_FR_NAME}" \
      --global --load-balancing-scheme=EXTERNAL_MANAGED --address="${IP_NAME}" \
      --target-http-proxy="${HTTP_PROXY_NAME}" --ports=80

  # IAP 有効化 (カスタム OAuth クライアント設定済みの場合は上書きしない)
  local client_id
  client_id="$("${GC[@]}" compute backend-services describe "${BACKEND_NAME}" --global \
    --format="value(iap.oauth2ClientId)")"
  if [[ -z "${client_id}" ]]; then
    log "Backend Service の IAP を有効化 (Google 管理 OAuth クライアント)"
    "${GC[@]}" compute backend-services update "${BACKEND_NAME}" --global --iap=enabled
  else
    log "IAP はカスタム OAuth クライアント (${client_id}) で設定済みのためスキップ"
  fi

  log "IAP サービスエージェントに roles/run.invoker を付与"
  "${GC[@]}" beta services identity create --service=iap.googleapis.com >/dev/null 2>&1 || true
  local project_number
  project_number="$("${GC[@]}" projects describe "${PROJECT_ID}" --format="value(projectNumber)")"
  "${GC[@]}" run services add-iam-policy-binding "${SERVICE_NAME}" --region="${REGION}" \
    --member="serviceAccount:service-${project_number}@gcp-sa-iap.iam.gserviceaccount.com" \
    --role="roles/run.invoker" >/dev/null

  log "LB IP: $(get_ip)"
}

cmd_dns() {
  local ip fqdn current
  ip="$(get_ip)"
  fqdn="${DOMAIN%.}."
  current="$(gcloud dns record-sets list --project="${DNS_PROJECT}" --zone="${DNS_ZONE}" \
    --name="${fqdn}" --type=A --format="value(rrdatas[0])" 2>/dev/null || true)"
  if [[ "${current}" == "${ip}" ]]; then
    log "DNS A ${fqdn} -> ${ip}: 設定済み"
  elif [[ -z "${current}" ]]; then
    log "DNS A ${fqdn} -> ${ip}: 作成 (${DNS_PROJECT}/${DNS_ZONE})"
    gcloud dns record-sets create "${fqdn}" --project="${DNS_PROJECT}" --zone="${DNS_ZONE}" \
      --type=A --ttl=300 --rrdatas="${ip}"
  else
    log "DNS A ${fqdn}: ${current} -> ${ip} に更新"
    gcloud dns record-sets update "${fqdn}" --project="${DNS_PROJECT}" --zone="${DNS_ZONE}" \
      --type=A --ttl=300 --rrdatas="${ip}"
  fi
}

cmd_switch_run() {
  log "Cloud Run: Native IAP 無効化 & ingress を internal-and-cloud-load-balancing に制限"
  "${GC[@]}" run services update "${SERVICE_NAME}" --region="${REGION}" \
    --no-iap --ingress=internal-and-cloud-load-balancing
}

cmd_oauth_client() {
  : "${OAUTH_CLIENT_ID:?OAUTH_CLIENT_ID is required}"
  : "${OAUTH_CLIENT_SECRET:?OAUTH_CLIENT_SECRET is required}"
  log "Backend Service ${BACKEND_NAME} にカスタム OAuth クライアントを設定"
  "${GC[@]}" compute backend-services update "${BACKEND_NAME}" --global \
    --iap="enabled,oauth2-client-id=${OAUTH_CLIENT_ID},oauth2-client-secret=${OAUTH_CLIENT_SECRET}"
}

cmd_add_user() {
  : "${IAP_USER:?IAP_USER is required (例: IAP_USER=someone@example.com)}"
  local member="${IAP_USER}" email
  [[ "${member}" == *:* ]] || member="user:${member}"
  email="${member#*:}"

  log "IAP 権限付与: ${member} -> ${BACKEND_NAME}"
  "${GC[@]}" iap web add-iam-policy-binding --resource-type=backend-services \
    --service="${BACKEND_NAME}" --member="${member}" --role="roles/iap.httpsResourceAccessor" >/dev/null

  # アプリ側ホワイトリスト (IAP_USERS) はユーザー/グループ単位のメールのみ対象
  if [[ "${member}" != user:* ]]; then
    log "${member} はユーザー以外のため IAP_USERS への追記はスキップ"
    return
  fi
  local current
  current="$("${GC[@]}" run services describe "${SERVICE_NAME}" --region="${REGION}" --format=json |
    python3 -c 'import json,sys; env=json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env",[]); print(next((e.get("value","") for e in env if e["name"]=="IAP_USERS"),""))')"
  if [[ ",${current}," == *",${email},"* ]]; then
    log "IAP_USERS に ${email} は登録済み"
    return
  fi
  local updated="${current:+${current},}${email}"
  log "IAP_USERS を更新: ${updated}"
  # 値にカンマを含むため区切り文字を | に変更
  "${GC[@]}" run services update "${SERVICE_NAME}" --region="${REGION}" \
    --update-env-vars="^|^IAP_USERS=${updated}"
}

cmd_status() {
  log "証明書"
  "${GC[@]}" compute ssl-certificates describe "${CERT_NAME}" --global \
    --format="table(name,managed.status,managed.domainStatus)"
  log "DNS"
  gcloud dns record-sets list --project="${DNS_PROJECT}" --zone="${DNS_ZONE}" \
    --name="${DOMAIN%.}." --format="table(name,type,rrdatas)"
  log "Backend IAP"
  "${GC[@]}" compute backend-services describe "${BACKEND_NAME}" --global \
    --format="table(name,iap.enabled,iap.oauth2ClientId)"
  log "IAP ポリシー"
  "${GC[@]}" iap web get-iam-policy --resource-type=backend-services --service="${BACKEND_NAME}"
}

case "${1:-}" in
  setup) cmd_setup ;;
  dns) cmd_dns ;;
  switch-run) cmd_switch_run ;;
  oauth-client) cmd_oauth_client ;;
  add-user) cmd_add_user ;;
  status) cmd_status ;;
  *) echo "usage: $0 {setup|dns|switch-run|oauth-client|add-user|status}" >&2; exit 1 ;;
esac
