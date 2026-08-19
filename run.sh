#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
NEO4J_CONTAINER="mpia-neo4j"
NEO4J_PORT=7687
APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"

# ---------- 颜色输出 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------- 虚拟环境 ----------
activate_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        err "虚拟环境不存在: $VENV_DIR，请先创建并安装依赖"
        exit 1
    fi
    source "$VENV_DIR/bin/activate"
    log "虚拟环境已激活: $VENV_DIR"

    # PaddleX 缓存目录：默认 ~/.paddlex 在部分环境下不可写，改到项目内
    export PADDLE_PDX_CACHE_HOME="${PADDLE_PDX_CACHE_HOME:-$PROJECT_DIR/.paddlex-cache}"
    mkdir -p "$PADDLE_PDX_CACHE_HOME"
    log "PaddleX 缓存目录: $PADDLE_PDX_CACHE_HOME"

    # PaddleX 模型源固定为百度 bos（国内源，速度快），并跳过源连通性检查
    export PADDLE_PDX_MODEL_SOURCE="${PADDLE_PDX_MODEL_SOURCE:-bos}"
    export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="${PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK:-True}"
    # 禁用 oneDNN：paddle 3.3.0 CPU 后端存在 PIR/oneDNN 指令转换 bug，会导致 OCR 推理失败
    export PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT="${PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT:-False}"
    # ModelScope 缓存目录（避免 ~/.modelscope 不可写时反复告警）
    export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$PROJECT_DIR/.paddlex-cache/modelscope}"
    mkdir -p "$MODELSCOPE_CACHE"
    ensure_env
}

# ---------- Neo4j ----------
# 检测可用的 docker 命令
_detect_docker() {
    if docker ps &>/dev/null 2>&1; then
        echo "docker"
    elif sudo docker ps &>/dev/null 2>&1; then
        echo "sudo docker"
    else
        echo ""
    fi
}

start_neo4j() {
    local DOCKER
    DOCKER=$(_detect_docker)

    if [ -z "$DOCKER" ]; then
        # Docker daemon 未运行，尝试启动
        log "Docker daemon 未运行，尝试启动..."
        if sudo systemctl start docker 2>/dev/null; then
            sleep 1
            DOCKER=$(_detect_docker)
        fi
        if [ -z "$DOCKER" ]; then
            warn "无法连接 Docker，跳过 Neo4j（存储功能不可用）"
            return
        fi
    fi

    if $DOCKER ps --format '{{.Names}}' 2>/dev/null | grep -q "^${NEO4J_CONTAINER}$"; then
        log "Neo4j 容器已在运行"
        return
    fi

    if $DOCKER ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${NEO4J_CONTAINER}$"; then
        log "启动已存在的 Neo4j 容器..."
        $DOCKER start "$NEO4J_CONTAINER"
    else
        log "创建并启动 Neo4j 容器..."
        # 优先使用已拉取的镜像，其次尝试 DaoCloud 国内镜像，最后回退 Docker Hub
        NEO4J_IMAGE="neo4j:5"
        if $DOCKER images docker.m.daocloud.io/library/neo4j:5 --format 'x' 2>/dev/null | grep -q x; then
            NEO4J_IMAGE="docker.m.daocloud.io/library/neo4j:5"
        fi
        $DOCKER run -d \
            --name "$NEO4J_CONTAINER" \
            -p 7474:7474 -p 7687:7687 \
            -e NEO4J_AUTH=neo4j/mpia2025 \
            "$NEO4J_IMAGE"
    fi
}

wait_neo4j() {
    log "等待 Neo4j 就绪..."
    local max_retries=30
    local retry=0
    while [ $retry -lt $max_retries ]; do
        if curl -s -o /dev/null "http://localhost:7474" 2>/dev/null; then
            log "Neo4j 已就绪"
            return
        fi
        sleep 2
        retry=$((retry + 1))
    done
    warn "Neo4j 启动超时，继续启动应用（存储功能可能不可用）"
}

# ---------- FastAPI ----------
start_app() {
    cd "$PROJECT_DIR"
    log "启动 FastAPI 服务 (${APP_HOST}:${APP_PORT})..."
    "$VENV_DIR/bin/python" -m uvicorn app.main:app \
        --host "$APP_HOST" \
        --port "$APP_PORT" \
        --reload \
        --log-level info
}

stop_neo4j() {
    local DOCKER
    DOCKER=$(_detect_docker)
    if [ -z "$DOCKER" ]; then
        warn "Docker 不可用，无法停止 Neo4j 容器"
        return
    fi
    if $DOCKER ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${NEO4J_CONTAINER}$"; then
        log "停止 Neo4j 容器..."
        $DOCKER stop "$NEO4J_CONTAINER"
    else
        log "Neo4j 容器不存在"
    fi
}

# ---------- 主流程 ----------

# ---------- 环境变量兜底 ----------
# 远端提交可能新增必填配置（如 TIMEOUT / REAL_JSON_PATH_*），本地 .env 缺失时
# 直接启动会在 pydantic 校验阶段崩溃。这里在启动前自动补齐缺失项（不覆盖已有值）。
ensure_env() {
    local env_file="$PROJECT_DIR/.env"
    touch "$env_file"
    local added=0

    append_default() { # $1=key $2=value
        if ! grep -q "^${1}=" "$env_file"; then
            printf '%s=%s
' "$1" "$2" >> "$env_file"
            added=$((added + 1))
        fi
    }

    append_default TIMEOUT 120
    append_default VIDEO_MODEL "Qwen/Qwen3-VL-32B-Instruct"

    # RAG 数据集：优先使用 sample/ 下的完整数据集，缺失时回退旧占位目录
    if [ -f "$PROJECT_DIR/../sample/诈骗案例数据集_重分类.json" ]; then
        append_default REAL_JSON_PATH_1 "../sample/诈骗案例数据集_重分类.json"
    else
        append_default REAL_JSON_PATH_1 "Knowledge_base/raw"
    fi
    if [ -f "$PROJECT_DIR/../sample/对话数据集.json" ]; then
        append_default REAL_JSON_PATH_2 "../sample/对话数据集.json"
    else
        append_default REAL_JSON_PATH_2 "Knowledge_base/raw"
    fi
    append_default REAL_JSON_PROCESSED "Knowledge_base/processed"
    mkdir -p "$PROJECT_DIR/Knowledge_base/processed"

    if [ $added -gt 0 ]; then
        log "已补齐 .env 缺失配置项（$added 项），若需要可手动调整"
    fi
    if [ ! -f "$PROJECT_DIR/../sample/诈骗案例数据集_重分类.json" ]; then
        warn "未找到 RAG 完整数据集（sample/诈骗案例数据集_重分类.json），研判将缺少相似案例参考"
    fi
}
main() {
    case "${1:-start}" in
        start)
            echo ""
            log "============================================"
            log "  多模态警务智能研判系统 - 启动脚本"
            log "============================================"
            echo ""

            activate_venv
            start_neo4j
            wait_neo4j
            start_app
            ;;
        stop)
            echo ""
            log "============================================"
            log "  多模态警务智能研判系统 - 停止脚本"
            log "============================================"
            echo ""
            stop_neo4j
            ;;
        *)
            err "未知命令: $1（支持: start | stop）"
            exit 1
            ;;
    esac
}

main "$@"
