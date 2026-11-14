# AIOps Multi-Agent Platform Makefile

.PHONY: help install dev test lint deploy setup-iap playground clean \
	lb-setup lb-dns lb-switch-run lb-all lb-status iap-set-oauth-client iap-add-user

SHELL := /bin/bash

PORT ?= 8080
PROJECT_ID ?= $(shell gcloud config get-value project 2>/dev/null || echo "your-project-id")
PROJECT_NUMBER ?= $(shell gcloud projects describe $(PROJECT_ID) --format="value(projectNumber)" 2>/dev/null)
REGION ?= us-central1
SERVICE_NAME ?= aiops-multi-agent
IAP_USERS ?=

# Global External Application LB + IAP 設定 (lb-* / iap-* ターゲットで使用。DOMAIN と DNS_ZONE は必須)
# 例: make lb-all DOMAIN=aiops.example.com DNS_ZONE=your-dns-zone
DOMAIN ?=
DNS_PROJECT ?= $(PROJECT_ID)
DNS_ZONE ?=
LB_PREFIX ?= aiops
LB_ENV := PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) SERVICE_NAME=$(SERVICE_NAME) \
	DOMAIN=$(DOMAIN) DNS_PROJECT=$(DNS_PROJECT) DNS_ZONE=$(DNS_ZONE) LB_PREFIX=$(LB_PREFIX)
LB_SCRIPT := bash deployment/scripts/gclb_iap.sh

# IAP_USERS は `make iap-add-user` で管理するため、deploy では上書きしない
ENV_VARS := GOOGLE_CLOUD_PROJECT=$(PROJECT_ID),GOOGLE_CLOUD_LOCATION=$(REGION),AIOPS_MODEL=gemini-3.8-flash

help: ## 利用可能な Makefile ターゲット一覧を表示
	@echo "AIOps Multi-Agent Platform - 利用可能なコマンド:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## uv による仮想環境と依存関係のセットアップ
	uv sync --frozen

dev: ## ローカル開発サーバーの起動 (FastAPI + ADK 2.x + Web UI on Port 8080)
	PORT=$(PORT) uv run --frozen python -m uvicorn app.fast_api_app:app --host 0.0.0.0 --port $(PORT) --reload

test: ## pytest による単体・統合テストの実行
	uv run --frozen pytest tests/unit/

lint: ## ruff によるコード品質チェック
	uv run --frozen ruff check app tests

playground: ## ADK 公式 Dev UI (Playground) の起動 on Port 8000
	uv run --frozen adk web . --port 8000

setup-iap: ## [旧] Cloud Run Native IAP 用の IAM 設定 (GCLB 構成では lb-setup / iap-add-user を使用)
	@echo "IAP サービスエージェント作成および権限付与を実行します (Project: $(PROJECT_ID))..."
	@gcloud services identity create --service=iap.googleapis.com --project=$(PROJECT_ID) 2>/dev/null || true
	@if [ -n "$(PROJECT_NUMBER)" ]; then \
		echo "IAP サービスエージェント (service-$(PROJECT_NUMBER)@gcp-sa-iap.iam.gserviceaccount.com) に roles/run.invoker を付与..."; \
		gcloud run services add-iam-policy-binding $(SERVICE_NAME) \
			--project=$(PROJECT_ID) \
			--region=$(REGION) \
			--member="serviceAccount:service-$(PROJECT_NUMBER)@gcp-sa-iap.iam.gserviceaccount.com" \
			--role="roles/run.invoker" || true; \
	fi
	@if [ -n "$(IAP_USERS)" ]; then \
		echo "指定された IAP_USERS に roles/iap.httpsResourceAccessor を付与..."; \
		for user in $$(echo $(IAP_USERS) | tr ',' ' '); do \
			member="$$user"; \
			if [[ "$$member" != *:* ]]; then \
				member="user:$$member"; \
			fi; \
			echo "  -> $$member"; \
			gcloud iap web add-iam-policy-binding \
				--project=$(PROJECT_ID) \
				--resource-type=cloud-run \
				--service=$(SERVICE_NAME) \
				--region=$(REGION) \
				--member="$$member" \
				--role="roles/iap.httpsResourceAccessor"; \
		done; \
	fi
	@echo "setup-iap 完了。"

deploy: ## Cloud Run へのデプロイ (GCLB + IAP 経由のみ受付 / コンテナビルド & デプロイ)
	@echo "Deploying $(SERVICE_NAME) to Cloud Run (ingress: LB only) in $(REGION) (Project: $(PROJECT_ID))..."
	gcloud run deploy $(SERVICE_NAME) \
		--source . \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--port 8080 \
		--no-allow-unauthenticated \
		--no-iap \
		--ingress internal-and-cloud-load-balancing \
		--min-instances 1 \
		--max-instances 5 \
		--memory 1Gi \
		--cpu 1 \
		--update-env-vars="$(ENV_VARS)"

lb-setup: ## GCLB 一式 (静的IP/証明書/NEG/Backend/URL Map/Proxy/FR) 作成 & IAP 有効化
	$(LB_ENV) $(LB_SCRIPT) setup

lb-dns: ## DNS_PROJECT/DNS_ZONE に DOMAIN の A レコードを作成・更新
	$(LB_ENV) $(LB_SCRIPT) dns

lb-switch-run: ## Cloud Run Native IAP を無効化し ingress を LB 経由のみに制限
	$(LB_ENV) $(LB_SCRIPT) switch-run

lb-all: lb-setup lb-dns lb-switch-run ## GCLB + IAP への切替を一括実行

lb-status: ## 証明書・DNS・IAP 設定の状態確認
	$(LB_ENV) $(LB_SCRIPT) status

iap-set-oauth-client: ## 外部アカウント許可用カスタム OAuth クライアントを設定 (OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET 必須)
	@$(LB_ENV) OAUTH_CLIENT_ID="$(OAUTH_CLIENT_ID)" OAUTH_CLIENT_SECRET="$(OAUTH_CLIENT_SECRET)" $(LB_SCRIPT) oauth-client

iap-add-user: ## IAP ユーザー追加 (IAP 権限 + アプリ側 IAP_USERS) 例: make iap-add-user IAP_USER=someone@example.com
	$(LB_ENV) IAP_USER="$(IAP_USER)" $(LB_SCRIPT) add-user

clean: ## キャッシュおよび一時ファイルの削除
	rm -rf .pytest_cache app/__pycache__ tests/__pycache__
